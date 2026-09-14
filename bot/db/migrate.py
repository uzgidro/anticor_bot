"""Programmatic Alembic upgrade — run ``alembic upgrade head`` from code.

This lets the bot bring the schema up to head on startup (see
``RUN_MIGRATIONS_ON_STARTUP``) without a separate one-shot container, which is
convenient for dev and single-replica deployments.

Alembic's ``env.py`` runs its own ``asyncio.run`` for online migrations, so
``command.upgrade`` is a *synchronous* call that owns an event loop. Calling it
from within our async ``main()`` would nest event loops; ``run_upgrade_to_head``
therefore offloads it to a worker thread via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command

logger = logging.getLogger(__name__)

# alembic.ini lives at the project root (two levels up from this file: bot/db/).
_INI_PATH = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _config(dsn: str) -> Config:
    cfg = Config(str(_INI_PATH))
    # script_location in the ini is relative to the project root; make it
    # absolute so the upgrade works regardless of the process cwd.
    cfg.set_main_option("script_location", str(_INI_PATH.parent / "alembic"))
    # env.py reads the DSN from app config by default; override it explicitly so
    # callers (and tests) can target a specific database.
    cfg.set_main_option("sqlalchemy.url", dsn)
    # Leave logging to the app (see alembic/env.py); the ini's logging section
    # would reset the root logger and drop the PII-redacting handler.
    cfg.attributes["configure_logging"] = False
    return cfg


def upgrade_to_head(dsn: str) -> None:
    """Synchronously upgrade the database at ``dsn`` to the latest revision.

    Idempotent: a no-op when already at head. ``dsn`` may be a sync or async
    SQLAlchemy URL — Alembic's env builds its own async engine for online runs.
    """
    command.upgrade(_config(dsn), "head")


async def run_upgrade_to_head(dsn: str, *, retries: int = 10, delay: float = 1.0) -> None:
    """Await the upgrade, running the sync (own-event-loop) call off-thread.

    On a shared web panel the database may still be warming up when the bot
    container starts; transient connect failures are retried up to ``retries``
    times with ``delay`` seconds between attempts. The last failure propagates
    so the container restarts rather than running half-initialised.
    """
    for attempt in range(1, retries + 1):
        try:
            # Resolve via the module so tests can monkeypatch upgrade_to_head.
            await asyncio.to_thread(sys.modules[__name__].upgrade_to_head, dsn)
            return
        except Exception as exc:  # noqa: BLE001 — retry any startup-time failure
            if attempt >= retries:
                raise
            logger.warning(
                "Migration attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, retries, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
