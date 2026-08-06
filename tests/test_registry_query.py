"""Registry query: filtering, ordering, pagination, and the total count.

The registry must never widen visibility, so type filtering is asserted here
as well as in the access tests.
"""
from datetime import UTC, datetime, timedelta

import pytest

from bot.db.models import Submission, SubmissionStatus, SubmissionType
from bot.db.repositories import SubmissionRepository

_BASE = datetime(2026, 6, 1, tzinfo=UTC)


async def _seed(session, specs):
    """specs: list of (type_, status, days_offset). Returns created rows."""
    rows = []
    for i, (type_, status, offset) in enumerate(specs):
        sub = Submission(
            type=type_,
            text=f"text {i}",
            is_anonymous=False,
            public_id=f"PID{i:05d}",
            ticket_number=f"TKT-2026-{i:04d}",
            status=status,
            created_at=_BASE + timedelta(days=offset),
        )
        session.add(sub)
        rows.append(sub)
    await session.flush()
    return rows


@pytest.mark.asyncio
async def test_filters_by_type(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.corruption, SubmissionStatus.new, 1),
    ])
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal
    )
    assert total == 1
    assert [r.type for r in rows] == [SubmissionType.appeal]


@pytest.mark.asyncio
async def test_filters_by_status(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.appeal, SubmissionStatus.closed, 1),
    ])
    repo = SubmissionRepository(session)
    rows, total = await repo.list_for_registry(
        type_=SubmissionType.appeal, status=SubmissionStatus.closed
    )
    assert total == 1
    assert rows[0].status == SubmissionStatus.closed


@pytest.mark.asyncio
async def test_status_none_returns_all_statuses(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.appeal, SubmissionStatus.in_progress, 1),
        (SubmissionType.appeal, SubmissionStatus.closed, 2),
    ])
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, status=None
    )
    assert total == 3


@pytest.mark.asyncio
async def test_order_desc_is_newest_first(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),   # oldest
        (SubmissionType.appeal, SubmissionStatus.new, 5),   # newest
    ])
    rows, _ = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, order="desc"
    )
    assert rows[0].created_at > rows[1].created_at


@pytest.mark.asyncio
async def test_order_asc_is_oldest_first(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.appeal, SubmissionStatus.new, 5),
    ])
    rows, _ = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, order="asc"
    )
    assert rows[0].created_at < rows[1].created_at


@pytest.mark.asyncio
async def test_pagination_slices_and_reports_full_total(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, i) for i in range(12)
    ])
    repo = SubmissionRepository(session)
    page0, total = await repo.list_for_registry(type_=SubmissionType.appeal, page=0)
    page2, _ = await repo.list_for_registry(type_=SubmissionType.appeal, page=2)
    assert total == 12          # total is the FULL count, not the page length
    assert len(page0) == 5      # default per_page
    assert len(page2) == 2      # last partial page
    assert {r.id for r in page0}.isdisjoint({r.id for r in page2})


@pytest.mark.asyncio
async def test_page_beyond_end_is_empty_not_error(session):
    await _seed(session, [(SubmissionType.appeal, SubmissionStatus.new, 0)])
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, page=99
    )
    assert rows == []
    assert total == 1
