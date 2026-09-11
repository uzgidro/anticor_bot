"""Tests for the programmatic Alembic upgrade helper (bot.db.migrate).

We verify the helper actually drives Alembic to head against a fresh database
and creates the expected tables. SQLite (file-backed) is used here so the test
needs no network/containers — the concern under test is the wiring (locating
alembic.ini, overriding the DSN, running upgrade), not Postgres specifics.
"""
import sqlite3
from pathlib import Path

import pytest

from bot.db.migrate import run_upgrade_to_head, upgrade_to_head


def _sqlite_url(tmp_path: Path) -> tuple[str, str]:
    """Return (sync_url, db_path) for a fresh sqlite file under tmp_path."""
    db_path = tmp_path / "test.db"
    return f"sqlite:///{db_path}", str(db_path)


def _table_names(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_upgrade_to_head_creates_schema(tmp_path):
    """Running the helper brings an empty DB up to head with all tables."""
    url, db_path = _sqlite_url(tmp_path)

    upgrade_to_head(url)

    tables = _table_names(db_path)
    assert "users" in tables
    assert "submissions" in tables
    assert "alembic_version" in tables  # Alembic stamped the revision


def test_upgrade_to_head_is_idempotent(tmp_path):
    """Calling twice must not error (already at head -> no-op)."""
    url, _ = _sqlite_url(tmp_path)
    upgrade_to_head(url)
    upgrade_to_head(url)  # should be a no-op, not raise


@pytest.mark.asyncio
async def test_run_upgrade_to_head_async_wrapper(tmp_path):
    """The async wrapper runs the (sync, own-event-loop) upgrade off-thread."""
    url, db_path = _sqlite_url(tmp_path)
    await run_upgrade_to_head(url)
    assert "submissions" in _table_names(db_path)


@pytest.mark.asyncio
async def test_run_upgrade_retries_until_db_ready(tmp_path, monkeypatch):
    """A not-yet-ready DB (transient connect error) is retried, then succeeds."""
    import bot.db.migrate as migrate_mod

    calls = {"n": 0}
    real = migrate_mod.upgrade_to_head

    def flaky(dsn: str) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection refused")  # DB still warming up
        real(dsn)

    monkeypatch.setattr(migrate_mod, "upgrade_to_head", flaky)
    url, db_path = _sqlite_url(tmp_path)
    # Tight retry timing so the test stays fast.
    await migrate_mod.run_upgrade_to_head(url, retries=5, delay=0.01)
    assert calls["n"] == 3
    assert "submissions" in _table_names(db_path)


@pytest.mark.asyncio
async def test_run_upgrade_gives_up_after_retries(tmp_path, monkeypatch):
    """If the DB never comes up, the error propagates after exhausting retries."""
    import bot.db.migrate as migrate_mod

    def always_fail(dsn: str) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(migrate_mod, "upgrade_to_head", always_fail)
    url, _ = _sqlite_url(tmp_path)
    with pytest.raises(OSError):
        await migrate_mod.run_upgrade_to_head(url, retries=2, delay=0.01)


def test_upgrade_adds_matrix_schema(tmp_path):
    """Revision 0002: users.matrix_id, nullable tg_id, matrix_deliveries."""
    url, db_path = _sqlite_url(tmp_path)
    upgrade_to_head(url)

    con = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row for row in con.execute("PRAGMA table_info(users)")}
        assert "matrix_id" in cols
        assert cols["tg_id"][3] == 0  # notnull flag == 0 -> nullable
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "matrix_deliveries" in tables
    finally:
        con.close()
