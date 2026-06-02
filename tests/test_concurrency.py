"""Concurrency tests against REAL Postgres.

SQLite cannot prove the atomicity of the claim/ticket logic, so these run on a
real Postgres. Two ways to provide it:

* ``TEST_PG_DSN`` env var (asyncpg DSN) — used directly. This is how CI / local
  Windows runs work (start a container with the docker CLI, point here).
* otherwise fall back to testcontainers (needs the Docker SDK to reach the
  daemon, which is unreliable under MSYS on Windows).

Skipped automatically if neither is available.
"""
import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.base import Base
from bot.db.models import SubmissionType

_PG_DSN = os.getenv("TEST_PG_DSN")

try:
    from testcontainers.postgres import PostgresContainer

    _HAVE_TC = True
except Exception:  # pragma: no cover
    _HAVE_TC = False

pytestmark = pytest.mark.skipif(
    not (_PG_DSN or _HAVE_TC), reason="no TEST_PG_DSN and testcontainers unavailable"
)


@pytest_asyncio.fixture
async def pg_pool():
    # Function-scoped so the engine's asyncpg connections live on the same event
    # loop as the test (pytest-asyncio gives each test a fresh loop).
    dsn = _PG_DSN
    container = None
    if dsn is None:
        container = PostgresContainer("postgres:16-alpine", driver="asyncpg")
        container.start()
        dsn = container.get_connection_url()

    engine = create_async_engine(dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        if container is not None:
            container.stop()


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_wins(pg_pool):
    from bot.db.repositories import SubmissionRepository, UserRepository

    user_ids: list[int] = []
    async with pg_pool() as s:
        urepo = UserRepository(s)
        for tg in range(100, 110):
            u, _ = await urepo.get_or_create(tg_id=tg)
            await s.flush()
            user_ids.append(u.id)
        sub = await SubmissionRepository(s).create(
            type=SubmissionType.appeal, text="t", is_anonymous=False,
            public_id="CONC1", ticket_number="OBR-2026-1001",
        )
        await s.commit()
        sub_id = sub.id

    async def claim(uid: int) -> bool:
        async with pg_pool() as s:
            repo = SubmissionRepository(s)
            won = await repo.try_claim(sub_id, user_id=uid)
            await s.commit()
            return won

    results = await asyncio.gather(*[claim(uid) for uid in user_ids])
    assert sum(results) == 1  # exactly one winner


@pytest.mark.asyncio
async def test_concurrent_ticket_numbers_unique(pg_pool):
    from bot.db.repositories import SubmissionRepository

    async def gen() -> str:
        async with pg_pool() as s:
            repo = SubmissionRepository(s)
            num = await repo.next_ticket_number(SubmissionType.corruption, year=2027)
            await s.commit()
            return num

    nums = await asyncio.gather(*[gen() for _ in range(25)])
    assert len(set(nums)) == len(nums)  # no duplicates under concurrency
