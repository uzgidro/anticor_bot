"""ThrottlingMiddleware — per-user rate limit backed by Redis.

A public government intake bot must resist spam/flood, so this is mandatory
(not optional). Uses a short Redis key with TTL as a token-bucket-ish gate:
at most one accepted update per ``rate`` seconds per user; excess updates are
dropped with a localized notice (at most once per window).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import Redis


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis, rate: float = 0.5) -> None:
        self.redis = redis
        self.rate = rate

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        key = f"throttle:{tg_user.id}"
        # SET NX with TTL: first hit in the window succeeds, rest are blocked.
        allowed = await self.redis.set(key, 1, ex=max(1, int(self.rate)), nx=True)
        if allowed:
            return await handler(event, data)

        # Notify at most once per window (separate flag).
        notify_key = f"throttle-note:{tg_user.id}"
        if await self.redis.set(notify_key, 1, ex=max(1, int(self.rate)), nx=True):
            i18n = data.get("i18n")
            text = i18n.get("throttled") if i18n else "⏳ Too many requests."
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=False)
        return None
