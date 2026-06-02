"""Entry point: build everything and run polling or webhook based on config."""
from __future__ import annotations

import asyncio
import logging

from bot.config import Settings
from bot.db.migrate import run_upgrade_to_head
from bot.factory import build
from bot.runners.commands import set_commands
from bot.runners.polling import run_polling
from bot.runners.webhook import run_webhook
from bot.security.logging import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()
    setup_logging(debug=settings.debug)

    # Bring the schema up to head before anything touches the DB. On a shared
    # web panel (no compose / no separate one-shot job) this is the only place
    # migrations can run. Disable via RUN_MIGRATIONS_ON_STARTUP=false when a
    # dedicated migration step exists (e.g. multiple replicas).
    if settings.run_migrations_on_startup:
        logger.info("Applying database migrations (upgrade head)...")
        await run_upgrade_to_head(settings.postgres.dsn)
        logger.info("Migrations applied.")

    bot, dp, redis, engine = build(settings)
    core = dp["i18n_core"]
    try:
        await core.startup()
        await set_commands(bot, core, settings.locales, settings.default_locale)
        if settings.use_webhook:
            await run_webhook(bot, dp, settings)
        else:
            await run_polling(bot, dp, settings)
    finally:
        # Always release resources even if startup (set_commands etc.) fails.
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
