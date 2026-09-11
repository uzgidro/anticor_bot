"""Composition root: build Bot, Dispatcher, Redis, engine and i18n, and wire
middlewares in the strict order required by this bot.

Middleware order (outer): DbSession -> User(get-or-create) -> i18n -> Throttling.
The i18n manager reads ``db_user.language``, so the user must exist first; the
session must exist before the user. Throttling can localize its notice, so it
runs after i18n.
"""
from __future__ import annotations

from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram_i18n import I18nMiddleware
from aiogram_i18n.cores.fluent_runtime_core import FluentRuntimeCore
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.config import Settings
from bot.db.session import create_engine, create_session_pool
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.i18n_manager import DBLocaleManager
from bot.middlewares.media_group import MediaGroupMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.user import UserMiddleware

_LOCALES_PATH = str(Path(__file__).parent / "locales" / "{locale}" / "LC_MESSAGES")


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis.dsn)


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    settings: Settings,
    engine: AsyncEngine,
    session_pool: async_sessionmaker,
    redis: Redis,
) -> Dispatcher:
    from datetime import timedelta

    from bot.handlers import router as root_router

    # TTL on FSM state/data: abandoned form drafts (which may hold PII) must not
    # live in Redis forever. with_bot_id avoids key clashes if the same Redis DB
    # ever serves another bot token.
    storage = RedisStorage(
        redis=redis,
        key_builder=DefaultKeyBuilder(with_bot_id=True),
        state_ttl=timedelta(hours=6),
        data_ttl=timedelta(hours=6),
    )
    dp = Dispatcher(storage=storage)

    # i18n core + middleware (reads locale from db_user via DBLocaleManager).
    core = FluentRuntimeCore(path=_LOCALES_PATH)
    i18n_middleware = I18nMiddleware(
        core=core,
        manager=DBLocaleManager(default_locale=settings.default_locale),
    )

    # Outer middleware (registration order): DB session -> user (get-or-create)
    # -> i18n (needs db_user to resolve locale).
    dp.update.outer_middleware(DbSessionMiddleware(session_pool))
    dp.update.outer_middleware(UserMiddleware(admin_ids=settings.admin_ids))
    i18n_middleware.setup(dispatcher=dp)

    # Throttling is registered on message/callback as INNER middleware so it runs
    # AFTER aiogram's FSMContextMiddleware — it needs ``state`` in data to exempt
    # users who are mid-form (their multi-step input must never be dropped).
    throttle = ThrottlingMiddleware(redis=redis)
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)

    # Album aggregation only applies to messages.
    dp.message.middleware(MediaGroupMiddleware())

    dp.include_router(root_router)

    # Register the global error handler on the root router with the i18n core so
    # the generic notice is localized to the user.
    from bot.handlers import errors as errors_handlers

    errors_handlers.register_errors(root_router, core, settings.default_locale)

    # Expose settings and the i18n core to handlers / startup.
    dp["settings"] = settings
    dp["i18n_core"] = core
    # Channels that display submission cards besides Telegram push cards. The
    # Matrix bridge appends itself at startup (see bot/__main__.py); handlers
    # receive this list by name and pass it to SubmissionActions.
    dp["card_sinks"] = []

    async def _on_shutdown() -> None:
        await redis.aclose()
        await engine.dispose()

    dp.shutdown.register(_on_shutdown)
    return dp


def build(settings: Settings) -> tuple[Bot, Dispatcher, Redis, AsyncEngine]:
    redis = create_redis(settings)
    # Never echo SQL: bound parameters would leak submission text/names/phones
    # into logs and deanonymize complainants.
    engine = create_engine(settings.postgres.dsn, echo=False)
    session_pool = create_session_pool(engine)
    bot = create_bot(settings)
    dp = create_dispatcher(settings, engine, session_pool, redis)
    return bot, dp, redis, engine
