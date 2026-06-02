"""Shared pytest fixtures: in-memory SQLite session for fast unit tests.

Concurrency-sensitive tests (atomic claim, ticket counter races) additionally
run against real Postgres via testcontainers — see test_concurrency.py.
"""
import asyncio
import sys

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.base import Base

# asyncpg + concurrency is unreliable on Windows' default Proactor loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        yield s
    await engine.dispose()
