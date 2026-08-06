"""Registry end-to-end through Dispatcher.feed_update.

Proves the wiring holds: command -> list -> filter -> open -> back, with the
filter and page surviving the round trip, and the role gate holding on the
real dispatch path rather than only in a direct handler call.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import Settings
from bot.db.base import Base
from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.keyboards.inline import RegistryCb

_DT = datetime(2026, 6, 2, tzinfo=UTC)
_BASE = datetime(2026, 6, 1, tzinfo=UTC)
_UID = 2000


class _StubI18n:
    """Renders params too, so assertions can see interpolated values."""

    def __init__(self):
        self.core = SimpleNamespace(get=lambda key, locale=None, **kw: key)

    def get(self, key, /, *args, **kwargs):
        if not kwargs:
            return key
        return f"{key} " + " ".join(str(v) for v in kwargs.values())

    async def set_locale(self, code, **kw):
        pass


class _StubI18nMiddleware:
    async def __call__(self, handler, event, data):
        data["i18n"] = _StubI18n()
        return await handler(event, data)


class _DbMiddleware:
    def __init__(self, pool):
        self.pool = pool

    async def __call__(self, handler, event, data):
        async with self.pool() as session:
            data["session"] = session
            data["db_user"] = await session.scalar(
                select(User).where(User.tg_id == _UID)
            )
            result = await handler(event, data)
            await session.commit()
            return result


@pytest_asyncio.fixture
async def harness():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        s.add(User(tg_id=_UID, language="ru", resp_appeal=True))
        # 7 appeals: odd index -> new (3 of them), even -> closed (4 of them).
        for i in range(7):
            s.add(Submission(
                type=SubmissionType.appeal, text=f"body {i}", is_anonymous=False,
                public_id=f"PID{i:05d}", ticket_number=f"TKT-2026-{i:04d}",
                status=SubmissionStatus.new if i % 2 else SubmissionStatus.closed,
                full_name=f"Author {i}", created_at=_BASE + timedelta(days=i),
            ))
        await s.commit()

    settings = Settings(
        _env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode(),
    )

    import importlib

    from aiogram import Router

    from bot.handlers import (
        admin,
        errors,
        my_submissions,
        registry,
        responsible,
        start,
        submission,
    )

    # Module-level routers are singletons; reload so each test gets a fresh tree.
    for mod in (errors, admin, responsible, registry, submission, my_submissions, start):
        importlib.reload(mod)
    root = Router(name="root-test")
    root.include_router(admin.router)
    root.include_router(responsible.router)
    root.include_router(registry.router)
    root.include_router(submission.router)
    root.include_router(my_submissions.router)
    root.include_router(start.router)
    errors.register_errors(root)

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(_DbMiddleware(pool))
    dp.update.outer_middleware(_StubI18nMiddleware())
    dp.include_router(root)
    dp["settings"] = settings

    bot = AsyncMock()
    bot.id = 1
    yield SimpleNamespace(dp=dp, bot=bot, pool=pool)
    await engine.dispose()


@pytest.fixture
def sent(monkeypatch):
    """Capture what handlers render, without touching the network."""
    from aiogram.types import Message

    captured = []

    async def _answer(self, text, **kwargs):
        captured.append((text, kwargs.get("reply_markup")))
        return self

    async def _edit(self, text, **kwargs):
        captured.append((text, kwargs.get("reply_markup")))
        return self

    monkeypatch.setattr(Message, "answer", _answer, raising=False)
    monkeypatch.setattr(Message, "edit_text", _edit, raising=False)
    return captured


def _cb(data: str, msg_id: int = 1):
    from aiogram.types import CallbackQuery, Chat, Message, Update
    from aiogram.types import User as TgUser

    user = TgUser(id=_UID, is_bot=False, first_name="R")
    chat = Chat(id=_UID, type="private")
    msg = Message(message_id=msg_id, chat=chat, from_user=user, date=_DT)
    return Update(
        update_id=msg_id,
        callback_query=CallbackQuery(
            id=str(msg_id), from_user=user, chat_instance="ci", data=data, message=msg
        ),
    )


def _text(text: str):
    from aiogram.types import Chat, Message, Update
    from aiogram.types import User as TgUser

    user = TgUser(id=_UID, is_bot=False, first_name="R")
    chat = Chat(id=_UID, type="private")
    return Update(
        update_id=999,
        message=Message(message_id=999, chat=chat, from_user=user, date=_DT, text=text),
    )


@pytest.mark.asyncio
async def test_command_opens_registry(harness, sent):
    await harness.dp.feed_update(harness.bot, _text("/appeals"))
    assert sent, "the /appeals command rendered nothing"
    text, markup = sent[-1]
    assert "registry-title" in text
    assert markup is not None


@pytest.mark.asyncio
async def test_status_filter_limits_the_rows(harness, sent):
    """7 seeded rows: 3 new (odd i), 4 closed. The 'new' filter must show 3."""
    data = RegistryCb(type="appeal", status="new", order="asc", page=0, open="").pack()
    await harness.dp.feed_update(harness.bot, _cb(data))
    text, _ = sent[-1]
    assert text.count("registry-item") == 3


@pytest.mark.asyncio
async def test_open_detail_then_back_restores_filter_and_page(harness, sent):
    open_cb = RegistryCb(
        type="appeal", status="new", order="desc", page=0, open="PID00001"
    ).pack()
    await harness.dp.feed_update(harness.bot, _cb(open_cb))
    detail_text, detail_kb = sent[-1]
    assert "body 1" in detail_text  # the detail view shows the body

    back = [
        b.callback_data
        for row in detail_kb.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith("reg:")
    ][0]
    unpacked = RegistryCb.unpack(back)
    assert (unpacked.status, unpacked.order, unpacked.open) == ("new", "desc", "")

    await harness.dp.feed_update(harness.bot, _cb(back, msg_id=2))
    list_text, _ = sent[-1]
    assert "registry-title" in list_text


@pytest.mark.asyncio
async def test_pagination_moves_to_the_next_page(harness, sent):
    """7 rows at 5 per page: page 1 holds the remaining 2."""
    data = RegistryCb(type="appeal", status="all", order="asc", page=1, open="").pack()
    await harness.dp.feed_update(harness.bot, _cb(data))
    text, _ = sent[-1]
    assert text.count("registry-item") == 2


@pytest.mark.asyncio
async def test_citizen_command_is_refused(harness, sent):
    """A user with no roles must not reach the registry via the raw command."""
    async with harness.pool() as s:
        user = await s.scalar(select(User).where(User.tg_id == _UID))
        user.resp_appeal = False
        await s.commit()

    await harness.dp.feed_update(harness.bot, _text("/appeals"))
    assert sent, "expected a refusal message"
    text, _ = sent[-1]
    assert text == "admin-only"
    assert "registry-item" not in text


@pytest.mark.asyncio
async def test_menu_button_opens_registry(harness, sent):
    from bot.keyboards.inline import MenuCb

    await harness.dp.feed_update(
        harness.bot, _cb(MenuCb(action="reg_appeal").pack())
    )
    text, _ = sent[-1]
    assert "registry-title" in text
