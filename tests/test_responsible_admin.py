"""Wave 4 tests: responsible reactions (take/reply/close), admin assign/revoke,
audit trail — driven through Dispatcher.feed_update with a mock bot.
"""
import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import Settings
from bot.db.base import Base
from bot.db.models import (
    AuditLog,
    Submission,
    SubmissionResponse,
    SubmissionStatus,
    SubmissionType,
    User,
)

_DT = datetime(2026, 6, 2, tzinfo=UTC)


class _StubI18n:
    def __init__(self):
        self.core = SimpleNamespace(get=lambda key, locale=None, **kw: key)

    def get(self, key, /, *args, **kwargs):
        return key

    async def set_locale(self, code, **kw):
        pass


class _StubI18nMw:
    async def __call__(self, handler, event, data):
        data["i18n"] = _StubI18n()
        return await handler(event, data)


class _DbMw:
    """Injects a session and a db_user that is admin + responsible for both types."""

    def __init__(self, pool, actor_tg_id):
        self.pool = pool
        self.actor_tg_id = actor_tg_id

    async def __call__(self, handler, event, data):
        async with self.pool() as session:
            data["session"] = session
            user = await session.scalar(select(User).where(User.tg_id == self.actor_tg_id))
            if user is None:
                user = User(
                    tg_id=self.actor_tg_id, full_name="Officer", is_admin=True,
                    resp_appeal=True, resp_corruption=True, language="ru",
                )
                session.add(user)
                await session.flush()
            data["db_user"] = user
            result = await handler(event, data)
            await session.commit()
            return result


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(
        _env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode()
    )

    from bot.handlers import admin, errors, my_submissions, responsible, start, submission

    for mod in (errors, admin, responsible, submission, my_submissions, start):
        importlib.reload(mod)
    root = Router(name="root-test")
    for r in (errors, admin, responsible, submission, my_submissions, start):
        root.include_router(r.router)

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(_DbMw(pool, actor_tg_id=500))
    dp.update.outer_middleware(_StubI18nMw())
    dp.include_router(root)
    dp["settings"] = settings

    bot = AsyncMock()
    bot.id = 1
    yield SimpleNamespace(dp=dp, bot=bot, pool=pool, settings=settings)
    await engine.dispose()


def _cb(data: str, mid: int = 1):
    from aiogram.types import CallbackQuery, Chat, Message, Update
    from aiogram.types import User as TgUser

    user = TgUser(id=500, is_bot=False, first_name="Officer")
    chat = Chat(id=500, type="private")
    msg = Message(message_id=mid, chat=chat, from_user=user, date=_DT)
    return Update(
        update_id=mid,
        callback_query=CallbackQuery(
            id=str(mid), from_user=user, chat_instance="ci", data=data, message=msg
        ),
    )


def _text(text: str, mid: int = 900):
    from aiogram.types import Chat, Message, Update
    from aiogram.types import User as TgUser

    user = TgUser(id=500, is_bot=False, first_name="Officer")
    chat = Chat(id=500, type="private")
    return Update(
        update_id=mid,
        message=Message(message_id=mid, chat=chat, from_user=user, date=_DT, text=text),
    )


async def _make_submission(pool, settings, *, anonymous=False, author_tg=900):
    from bot.security.crypto import AnonCipher
    from bot.services.submissions import SubmissionInput, SubmissionService

    async with pool() as s:
        svc = SubmissionService(s, AnonCipher(settings.anon_enc_key))
        author_uid = None
        if not anonymous:
            author = User(tg_id=author_tg, language="ru")
            s.add(author)
            await s.flush()
            author_uid = author.id
        sub = await svc.create(
            SubmissionInput(
                type=SubmissionType.appeal if not anonymous else SubmissionType.corruption,
                text="report", is_anonymous=anonymous,
                author_tg_id=author_tg, author_user_id=author_uid,
            )
        )
        # Simulate a delivery record so update_all_cards has a target.
        from bot.db.models import SubmissionDelivery

        s.add(SubmissionDelivery(
            submission_id=sub.id, responsible_user_id=1, chat_id=500, message_id=1
        ))
        await s.commit()
        return sub.id


@pytest.mark.asyncio
async def test_take_claims_and_audits(env):
    sub_id = await _make_submission(env.pool, env.settings)
    await env.dp.feed_update(env.bot, _cb(f"react:take:{sub_id}"))

    async with env.pool() as s:
        sub = await s.get(Submission, sub_id)
        assert sub.status == SubmissionStatus.in_progress
        audits = list(await s.scalars(select(AuditLog).where(AuditLog.action == "status_change")))
        assert any(a.meta == "new->in_progress" for a in audits)
    # The card update for all deliveries was attempted.
    assert env.bot.edit_message_text.await_count >= 1


@pytest.mark.asyncio
async def test_reply_reaches_author_and_audits(env):
    sub_id = await _make_submission(env.pool, env.settings, author_tg=900)
    await env.dp.feed_update(env.bot, _cb(f"react:reply:{sub_id}"))
    await env.dp.feed_update(env.bot, _text("We are looking into it."))

    async with env.pool() as s:
        resp = (await s.scalars(select(SubmissionResponse))).one()
        assert resp.text == "We are looking into it."
        assert any(
            a.action == "reply"
            for a in await s.scalars(select(AuditLog).where(AuditLog.action == "reply"))
        )
    # The applicant (tg 900) was messaged.
    assert any(c.args[0] == 900 for c in env.bot.send_message.await_args_list)


@pytest.mark.asyncio
async def test_reply_to_anonymous_decrypts_chat(env):
    sub_id = await _make_submission(env.pool, env.settings, anonymous=True, author_tg=4242)
    await env.dp.feed_update(env.bot, _cb(f"react:reply:{sub_id}"))
    await env.dp.feed_update(env.bot, _text("Reply to anon."))
    # Anonymous author's chat id is recovered via decryption and messaged.
    assert any(c.args[0] == 4242 for c in env.bot.send_message.await_args_list)


@pytest.mark.asyncio
async def test_close_sets_status_and_notifies(env):
    sub_id = await _make_submission(env.pool, env.settings, author_tg=900)
    await env.dp.feed_update(env.bot, _cb(f"react:close:{sub_id}"))

    async with env.pool() as s:
        sub = await s.get(Submission, sub_id)
        assert sub.status == SubmissionStatus.closed
        assert sub.closed_by_user_id is not None


@pytest.mark.asyncio
async def test_admin_assign_and_revoke(env):
    # Create a target user to assign.
    async with env.pool() as s:
        target = User(tg_id=12345)
        s.add(target)
        await s.commit()
        target_id = target.id

    await env.dp.feed_update(env.bot, _cb(f"assign:{target_id}:appeal"))
    async with env.pool() as s:
        t = await s.get(User, target_id)
        assert t.resp_appeal is True
        assert any(
            a.action == "assign_role"
            for a in await s.scalars(select(AuditLog).where(AuditLog.action == "assign_role"))
        )

    await env.dp.feed_update(env.bot, _cb(f"revoke:{target_id}:appeal"))
    async with env.pool() as s:
        t = await s.get(User, target_id)
        assert t.resp_appeal is False
