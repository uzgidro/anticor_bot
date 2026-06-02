"""Alembic environment.

DSN comes from app config (env vars). Models are imported so ``Base.metadata``
is populated for autogenerate. Online migrations run through the async engine
via ``connection.run_sync``; offline mode emits SQL without a DB connection.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Import models so their tables register on Base.metadata (else autogen is empty).
from bot.db import models  # noqa: F401
from bot.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_dsn() -> str:
    # An explicit URL (set by the programmatic helper or `-x`/ini) wins; fall
    # back to app config so plain `alembic upgrade head` from a shell still works.
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from bot.config import Settings

    return Settings().postgres.dsn


def _is_async_dsn(dsn: str) -> bool:
    # asyncpg/aiosqlite drivers need the async engine + run_sync path; plain
    # sync URLs (e.g. sqlite:///file.db) run through a normal engine.
    return "+asyncpg" in dsn or "+aiosqlite" in dsn


def run_migrations_offline() -> None:
    context.configure(
        url=_get_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(dsn: str) -> None:
    engine = create_async_engine(dsn, poolclass=None)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def _run_sync_migrations(dsn: str) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(dsn, poolclass=None)
    with engine.connect() as connection:
        _do_run_migrations(connection)
    engine.dispose()


def run_migrations_online() -> None:
    dsn = _get_dsn()
    if _is_async_dsn(dsn):
        asyncio.run(_run_async_migrations(dsn))
    else:
        _run_sync_migrations(dsn)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
