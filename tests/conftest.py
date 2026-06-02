"""Shared pytest fixtures: in-memory SQLite session for fast unit tests.

Concurrency-sensitive tests (atomic claim, ticket counter races) additionally
run against real Postgres via testcontainers — see test_concurrency.py.
"""
import asyncio
import sys

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db.base import Base

# asyncpg + concurrency is unreliable on Windows' default Proactor loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,  # share one in-memory DB across create_all + sessions
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        # Create the schema on the SAME connection the test will use. A separate
        # engine.begin() can land on a different connection/loop and leave the
        # session looking at an empty in-memory DB.
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        yield s
    await engine.dispose()
