"""ThrottlingMiddleware — per-user rate limit backed by Redis.

A public government intake bot must resist spam/flood, so this is mandatory.
Registered as the FIRST outer middleware so flood is rejected BEFORE any DB
session/user work. Uses a Redis key with millisecond TTL (so sub-second rates
work) as a simple gate: at most one accepted update per ``rate_ms`` per user.

Users actively filling in a form (any FSM state) are exempt, so legitimate
multi-step input is never dropped — the limiter only guards the unauthenticated
flood surface (menu taps, command spam, starting submissions).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import Redis


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis, rate_ms: int = 400) -> None:
        self.redis = redis
        self.rate_ms = rate_ms

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        # Exempt users mid-form: their steps are legitimate and must not drop.
        state: FSMContext | None = data.get("state")
        if state is not None and await state.get_state() is not None:
            return await handler(event, data)

        key = f"throttle:{tg_user.id}"
        # SET NX PX: first hit in the window succeeds, rest are blocked.
        allowed = await self.redis.set(key, 1, px=self.rate_ms, nx=True)
        if allowed:
            return await handler(event, data)

        # Notify at most once per window (separate flag).
        notify_key = f"throttle-note:{tg_user.id}"
        if await self.redis.set(notify_key, 1, px=self.rate_ms * 4, nx=True):
            i18n = data.get("i18n")
            text = i18n.get("throttled") if i18n else "⏳ Too many requests."
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=False)
        return None
