"""UserMiddleware — get-or-create the ORM User and expose it as ``db_user``.

Must run AFTER DbSessionMiddleware (needs the session) and BEFORE the i18n
middleware and role filters (they read the user's language / role flags).
Bootstrap admins from config get ``is_admin`` set on first sight.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repositories import UserRepository


class UserMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        if tg_user is not None and session is not None and not tg_user.is_bot:
            repo = UserRepository(session)
            user, _ = await repo.get_or_create(
                tg_id=tg_user.id,
                username=tg_user.username,
                full_name=tg_user.full_name,
            )
            if tg_user.id in self.admin_ids and not user.is_admin:
                user.is_admin = True
                await session.flush()
            data["db_user"] = user
        return await handler(event, data)
