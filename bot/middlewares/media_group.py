"""MediaGroupMiddleware — aggregate album (media group) messages.

Telegram delivers an album as N separate Message updates sharing a
``media_group_id``. This middleware buffers them for a short debounce window and
invokes the handler ONCE for the first message with the full list under
``album``; subsequent messages of the same group are swallowed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class MediaGroupMiddleware(BaseMiddleware):
    def __init__(self, latency: float = 0.6) -> None:
        self.latency = latency
        self._albums: dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.media_group_id is None:
            return await handler(event, data)

        group_id = event.media_group_id
        if group_id in self._albums:
            # Not the first message of the album — buffer and swallow.
            self._albums[group_id].append(event)
            return None

        self._albums[group_id] = [event]
        await asyncio.sleep(self.latency)
        album = self._albums.pop(group_id, [event])
        data["album"] = album
        return await handler(event, data)
