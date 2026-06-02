"""Long-polling runner (used for testing)."""
from __future__ import annotations

from aiogram import Bot, Dispatcher

from bot.config import Settings


async def run_polling(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
    await dp.start_polling(bot)
