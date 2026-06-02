"""Integration-ish tests for the citizen flow via Dispatcher.feed_update.

Uses an in-memory FSM, a stub i18n middleware (returns keys verbatim), the real
DB session middleware over SQLite, and a mock Bot so no network is touched. We
assert state transitions and that a submission is persisted with the right
anonymity invariants — not exact message text.
"""
from datetime import UTC, datetime
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
from bot.db.models import Submission, SubmissionType, User


class _StubI18n:
    """Minimal I18nContext stand-in: returns the key, ignores params."""

    def __init__(self):
        self.core = SimpleNamespace(get=lambda key, locale=None, **kw: key)

    def get(self, key, /, *args, **kwargs):
        return key

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
            user = await session.scalar(select(User).where(User.tg_id == 1000))
            if user is None:
                user = User(tg_id=1000, language="ru")
                session.add(user)
                await session.flush()
            data["db_user"] = user
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

    settings = Settings(
        _env_file=None, bot_token="1:x",
        anon_enc_key=Fernet.generate_key().decode(),
    )

    # Build a FRESH router tree per test (the module-level routers are
    # singletons and can't be attached to two dispatchers).
    import importlib

    from aiogram import Router

    from bot.handlers import admin, errors, my_submissions, responsible, start, submission

    for mod in (errors, admin, responsible, submission, my_submissions, start):
        importlib.reload(mod)
    root = Router(name="root-test")
    root.include_router(admin.router)
    root.include_router(responsible.router)
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


_DT = datetime(2026, 6, 2, tzinfo=UTC)
_UID = 1000


def _user_chat():
    from aiogram.types import Chat
    from aiogram.types import User as TgUser

    return TgUser(id=_UID, is_bot=False, first_name="T"), Chat(id=_UID, type="private")


def _cb(data: str, msg_id: int = 1):
    from aiogram.types import CallbackQuery, Message, Update

    user, chat = _user_chat()
    msg = Message(message_id=msg_id, chat=chat, from_user=user, date=_DT)
    return Update(
        update_id=msg_id,
        callback_query=CallbackQuery(
            id=str(msg_id), from_user=user, chat_instance="ci", data=data, message=msg
        ),
    )


def _text(text: str):
    from aiogram.types import Message, Update

    user, chat = _user_chat()
    return Update(
        update_id=999,
        message=Message(message_id=999, chat=chat, from_user=user, date=_DT, text=text),
    )


@pytest.mark.asyncio
async def test_anonymous_corruption_flow_persists_no_pii(harness):
    dp, bot, pool = harness.dp, harness.bot, harness.pool

    # Open corruption form -> choose anonymous -> enter text -> done -> submit.
    await dp.feed_update(bot, _cb("menu:corruption"))
    await dp.feed_update(bot, _cb("anon:1"))  # AnonCb value=True
    await dp.feed_update(bot, _text("They demanded a bribe."))
    await dp.feed_update(bot, _cb("form:done"))
    await dp.feed_update(bot, _cb("form:submit"))

    async with pool() as s:
        subs = list(await s.scalars(select(Submission)))
    assert len(subs) == 1
    sub = subs[0]
    assert sub.type == SubmissionType.corruption
    assert sub.is_anonymous is True
    assert sub.author_user_id is None  # anonymity enforced end-to-end
    assert sub.full_name is None and sub.phone is None
    assert sub.text == "They demanded a bribe."


@pytest.mark.asyncio
async def test_appeal_flow_keeps_author(harness):
    dp, bot, pool = harness.dp, harness.bot, harness.pool

    await dp.feed_update(bot, _cb("menu:appeal"))
    await dp.feed_update(bot, _text("Ivan Ivanov"))  # name
    await dp.feed_update(bot, _text("+998901112233"))  # phone
    await dp.feed_update(bot, _text("Water outage in my district."))  # text
    await dp.feed_update(bot, _cb("form:skip"))  # skip attachments
    await dp.feed_update(bot, _cb("form:submit"))

    async with pool() as s:
        sub = (await s.scalars(select(Submission))).one()
    assert sub.type == SubmissionType.appeal
    assert sub.is_anonymous is False
    assert sub.full_name == "Ivan Ivanov"
    assert sub.phone == "+998901112233"
    assert sub.author_user_id is not None
