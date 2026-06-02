"""Global error handler.

Logs ONLY non-sensitive identifiers (update id/type and exception class) — never
the update body, which may contain PII / an anonymous complainant's text. Tells
the user a generic message if we can reach their chat.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

router = Router(name="errors")
logger = logging.getLogger("bot.errors")


def register_errors(root: Router, core=None, default_locale: str = "ru") -> None:
    """Register the error handler on the ROOT router (attached to the dispatcher).

    A leaf sub-router's @errors() never fires for sibling routers — error events
    only propagate along the routers that actually handled the update. Binding to
    the root makes it global. ``core`` (the i18n core) lets us localize the
    generic notice to the user's language.
    """

    async def handler(event: ErrorEvent) -> bool:
        return await on_error(event, core, default_locale)

    root.errors.register(handler)


def _user_locale(update, default_locale: str) -> str:
    user = getattr(update, "event_from_user", None) if update else None
    return getattr(user, "language_code", None) or default_locale


async def on_error(event: ErrorEvent, core=None, default_locale: str = "ru") -> bool:
    update = event.update
    logger.error(
        "update_id=%s type=%s failed: %s",
        getattr(update, "update_id", "?"),
        update.event_type if update else "?",
        type(event.exception).__name__,
    )
    # Best-effort generic notice to the user (no details, no PII).
    try:
        if update.callback_query is not None:
            await update.callback_query.answer()
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if msg is not None and getattr(msg, "answer", None):
            locale = _user_locale(update, default_locale)
            text = core.get("error-generic", locale) if core else "⚠️ Error. Try again later."
            await msg.answer(text)
    except Exception:
        pass
    return True  # handled; don't re-raise
