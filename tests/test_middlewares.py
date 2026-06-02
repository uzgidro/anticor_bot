"""Tests for Wave 2 middlewares, i18n manager, and PII-safe logging."""
import asyncio
from types import SimpleNamespace

import pytest

# ---------- DbSessionMiddleware ----------

@pytest.mark.asyncio
async def test_db_middleware_injects_and_commits(session):
    from bot.middlewares.db import DbSessionMiddleware

    # Fake pool yielding our test session via async context manager.
    class _Pool:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_):
                    return session

                async def __aexit__(self_, *exc):
                    return False

            return _Ctx()

    mw = DbSessionMiddleware(_Pool())
    seen = {}

    async def handler(event, data):
        seen["session"] = data["session"]
        return "ok"

    result = await mw(handler, SimpleNamespace(), {})
    assert result == "ok"
    assert seen["session"] is session


@pytest.mark.asyncio
async def test_db_middleware_rolls_back_on_error(session):
    from bot.middlewares.db import DbSessionMiddleware

    rolled = {"back": False}

    class _Sess:
        async def commit(self_):
            ...

        async def rollback(self_):
            rolled["back"] = True

    class _Pool:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_):
                    return _Sess()

                async def __aexit__(self_, *exc):
                    return False

            return _Ctx()

    mw = DbSessionMiddleware(_Pool())

    async def handler(event, data):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await mw(handler, SimpleNamespace(), {})
    assert rolled["back"] is True


# ---------- UserMiddleware ----------

@pytest.mark.asyncio
async def test_user_middleware_get_or_create_and_admin(session):
    from bot.middlewares.user import UserMiddleware

    mw = UserMiddleware(admin_ids={555})
    tg_user = SimpleNamespace(id=555, username="boss", full_name="Boss", is_bot=False)
    data = {"event_from_user": tg_user, "session": session}

    captured = {}

    async def handler(event, d):
        captured["user"] = d["db_user"]
        return "ok"

    await mw(handler, SimpleNamespace(), data)
    user = captured["user"]
    assert user.tg_id == 555
    assert user.is_admin is True  # bootstrap admin promoted


@pytest.mark.asyncio
async def test_user_middleware_skips_bots(session):
    from bot.middlewares.user import UserMiddleware

    mw = UserMiddleware(admin_ids=set())
    tg_user = SimpleNamespace(id=1, username="b", full_name="B", is_bot=True)
    data = {"event_from_user": tg_user, "session": session}

    async def handler(event, d):
        return "db_user" in d

    assert await mw(handler, SimpleNamespace(), data) is False


# ---------- DBLocaleManager ----------

@pytest.mark.asyncio
async def test_locale_manager_reads_and_writes(session):
    from bot.db.repositories import UserRepository
    from bot.middlewares.i18n_manager import DBLocaleManager

    user, _ = await UserRepository(session).get_or_create(tg_id=7)
    mgr = DBLocaleManager(default_locale="ru")

    assert await mgr.get_locale(db_user=user) == "ru"  # falls back to default
    await mgr.set_locale("en", db_user=user, session=session)
    assert await mgr.get_locale(db_user=user) == "en"


@pytest.mark.asyncio
async def test_locale_manager_default_when_no_user():
    from bot.middlewares.i18n_manager import DBLocaleManager

    mgr = DBLocaleManager(default_locale="uz_latn")
    assert await mgr.get_locale(db_user=None) == "uz_latn"


# ---------- MediaGroupMiddleware ----------

def _msg(media_group_id):
    from aiogram.types import Message

    # Bypass validation to build a lightweight Message for middleware tests.
    return Message.model_construct(message_id=1, media_group_id=media_group_id)


@pytest.mark.asyncio
async def test_media_group_aggregates():
    from bot.middlewares.media_group import MediaGroupMiddleware

    mw = MediaGroupMiddleware(latency=0.05)
    calls = []

    async def handler(event, data):
        calls.append(data.get("album"))
        return "handled"

    msgs = [_msg("G") for _ in range(3)]

    async def feed(m):
        return await mw(handler, m, {})

    results = await asyncio.gather(*[feed(m) for m in msgs])
    # Exactly one handler invocation with all 3 messages; others swallowed.
    assert calls and len(calls) == 1
    assert len(calls[0]) == 3
    assert results.count("handled") == 1


@pytest.mark.asyncio
async def test_media_group_passthrough_non_album():
    from bot.middlewares.media_group import MediaGroupMiddleware

    mw = MediaGroupMiddleware(latency=0.05)

    async def handler(event, data):
        return "single"

    # A message with no media_group_id passes straight through.
    assert await mw(handler, _msg(None), {}) == "single"


# ---------- PII-safe logging ----------

def test_redact_phone_and_ids():
    from bot.security.logging import redact

    assert "[redacted-phone]" in redact("call +998 90 111 22 33 now")
    assert "[redacted-id]" in redact("user 123456789 did x")
    assert redact("hello world") == "hello world"


def test_logging_filter_scrubs(caplog):
    import logging

    from bot.security.logging import PiiRedactingFilter

    logger = logging.getLogger("test.pii")
    logger.addFilter(PiiRedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.pii"):
        logger.info("anon tg 987654321 submitted")
    assert "987654321" not in caplog.text
