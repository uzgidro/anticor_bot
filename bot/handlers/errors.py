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


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
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
        if msg is not None:
            await msg.answer("⚠️ Error. Please try again later.")
    except Exception:
        pass
    return True  # handled; don't re-raise
