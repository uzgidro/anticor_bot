"""The global error handler logs only identifiers — plus Telegram's own error
description, which is a fixed API string (never user content) and is what makes
a TelegramBadRequest diagnosable."""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from bot.handlers.errors import on_error


def _event(exc):
    query = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
    update = SimpleNamespace(
        update_id=7, event_type="callback_query", callback_query=query, message=None,
        event_from_user=SimpleNamespace(language_code="ru"),
    )
    return SimpleNamespace(update=update, exception=exc)


async def test_telegram_error_description_is_logged(caplog):
    exc = TelegramBadRequest(
        method=EditMessageText(text="x", chat_id=1, message_id=1),
        message="Bad Request: message can't be edited",
    )
    with caplog.at_level(logging.ERROR, logger="bot.errors"):
        assert await on_error(_event(exc)) is True
    line = caplog.records[-1].getMessage()
    assert "TelegramBadRequest" in line and "message can't be edited" in line
    assert "x" not in line.split("failed:")[0]  # no method payload leaks


async def test_plain_exception_logs_class_only(caplog):
    with caplog.at_level(logging.ERROR, logger="bot.errors"):
        await on_error(_event(RuntimeError("secret text 998901234567")))
    line = caplog.records[-1].getMessage()
    assert line.endswith("failed: RuntimeError")
    assert "secret" not in line
