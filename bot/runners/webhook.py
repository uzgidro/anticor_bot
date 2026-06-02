"""Webhook runner (production) using aiogram's built-in aiohttp integration.

TLS is expected to be terminated by a reverse proxy in front of this app; the
container port must NOT be published directly. The secret token is verified on
every request via the X-Telegram-Bot-Api-Secret-Token header.
"""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.config import Settings


async def _on_startup(bot: Bot, settings: Settings) -> None:
    await bot.set_webhook(
        url=settings.webhook_full_url,
        secret_token=settings.webhook_secret.get_secret_value(),
        drop_pending_updates=settings.drop_pending_updates,
    )


def build_app(bot: Bot, dp: Dispatcher, settings: Settings) -> web.Application:
    app = web.Application()
    app["bot"] = bot

    async def _startup(_: web.Application) -> None:
        await _on_startup(bot, settings)

    app.on_startup.append(_startup)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret.get_secret_value(),
    ).register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)
    return app


async def run_webhook(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    app = build_app(bot, dp, settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=settings.webhook_host, port=settings.webhook_port)
    await site.start()
    # Block until cancelled (e.g. SIGTERM).
    import asyncio

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
