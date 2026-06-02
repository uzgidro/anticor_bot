"""Locale manager that reads/writes the user's language from the DB.

aiogram_i18n inspects the manager method signatures and injects matching keys
from the handler ``data`` dict. We rely on ``UserMiddleware`` having placed the
ORM ``User`` under the ``db_user`` key before the i18n middleware runs.
"""
from __future__ import annotations

from aiogram_i18n.managers import BaseManager
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User


class DBLocaleManager(BaseManager):
    async def get_locale(self, db_user: User | None = None) -> str:
        if db_user is not None and db_user.language:
            return db_user.language
        return self.default_locale

    async def set_locale(
        self, locale: str, db_user: User | None = None, session: AsyncSession | None = None
    ) -> None:
        if db_user is not None and session is not None:
            db_user.language = locale
            await session.flush()
