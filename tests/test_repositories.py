"""Tests for bot.db.repositories — CRUD, ticket generation, atomic claim."""
import pytest

from bot.db.models import Submission, SubmissionStatus, SubmissionType


@pytest.mark.asyncio
async def test_user_get_or_create(session):
    from bot.db.repositories import UserRepository

    repo = UserRepository(session)
    u1, created1 = await repo.get_or_create(tg_id=100, username="a", full_name="A")
    await session.commit()
    assert created1 is True
    u2, created2 = await repo.get_or_create(tg_id=100, username="a", full_name="A")
    assert created2 is False
    assert u1.id == u2.id


@pytest.mark.asyncio
async def test_set_language(session):
    from bot.db.repositories import UserRepository

    repo = UserRepository(session)
    u, _ = await repo.get_or_create(tg_id=1)
    await repo.set_language(u, "uz_latn")
    await session.commit()
    assert u.language == "uz_latn"


@pytest.mark.asyncio
async def test_responsibles_by_type(session):
    from bot.db.repositories import UserRepository

    repo = UserRepository(session)
    a, _ = await repo.get_or_create(tg_id=1)
    b, _ = await repo.get_or_create(tg_id=2)
    c, _ = await repo.get_or_create(tg_id=3)
    a.resp_appeal = True
    b.resp_corruption = True
    c.resp_appeal = True
    await session.commit()

    appeal = await repo.responsibles_for(SubmissionType.appeal)
    corruption = await repo.responsibles_for(SubmissionType.corruption)
    assert {u.tg_id for u in appeal} == {1, 3}
    assert {u.tg_id for u in corruption} == {2}


@pytest.mark.asyncio
async def test_ticket_number_format_and_increment(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    t1 = await repo.next_ticket_number(SubmissionType.appeal, year=2026)
    t2 = await repo.next_ticket_number(SubmissionType.appeal, year=2026)
    t3 = await repo.next_ticket_number(SubmissionType.corruption, year=2026)
    await session.commit()
    assert t1 == "OBR-2026-0001"
    assert t2 == "OBR-2026-0002"
    assert t3 == "COR-2026-0001"  # separate counter per type


@pytest.mark.asyncio
async def test_ticket_counter_resets_per_year(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    await repo.next_ticket_number(SubmissionType.appeal, year=2025)
    t = await repo.next_ticket_number(SubmissionType.appeal, year=2026)
    await session.commit()
    assert t == "OBR-2026-0001"


@pytest.mark.asyncio
async def test_create_submission_and_public_id(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    sub = await repo.create(
        type=SubmissionType.appeal,
        text="hello",
        is_anonymous=False,
        author_user_id=None,
        full_name="Ivan",
        phone="+998901112233",
        public_id="ABCDEF",
        ticket_number="OBR-2026-0001",
    )
    await session.commit()
    assert sub.id is not None
    assert sub.status == SubmissionStatus.new
    assert sub.public_id == "ABCDEF"


@pytest.mark.asyncio
async def test_atomic_claim_success_then_fail(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    sub = await repo.create(
        type=SubmissionType.appeal, text="t", is_anonymous=False,
        public_id="P1", ticket_number="OBR-2026-0009",
    )
    await session.commit()

    won_first = await repo.try_claim(sub.id, user_id=10)
    await session.commit()
    assert won_first is True

    won_second = await repo.try_claim(sub.id, user_id=20)
    await session.commit()
    assert won_second is False  # already in_progress

    # Core UPDATE bypassed the ORM identity map; force a fresh load.
    refreshed = await session.get(Submission, sub.id, populate_existing=True)
    assert refreshed.status == SubmissionStatus.in_progress
    assert refreshed.assigned_to_user_id == 10


@pytest.mark.asyncio
async def test_anon_ref_roundtrip(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    sub = await repo.create(
        type=SubmissionType.corruption, text="t", is_anonymous=True,
        public_id="AN1", ticket_number="COR-2026-0050",
    )
    await session.flush()
    await repo.add_anon_ref(sub.id, b"encrypted-blob")
    await session.commit()

    assert await repo.get_anon_ref(sub.id) == b"encrypted-blob"


@pytest.mark.asyncio
async def test_get_anon_ref_none_when_absent(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    sub = await repo.create(
        type=SubmissionType.appeal, text="t", is_anonymous=False,
        public_id="NOREF", ticket_number="OBR-2026-0051",
    )
    await session.commit()
    assert await repo.get_anon_ref(sub.id) is None


@pytest.mark.asyncio
async def test_close_idempotent(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    sub = await repo.create(
        type=SubmissionType.appeal, text="t", is_anonymous=False,
        public_id="CL1", ticket_number="OBR-2026-0052",
    )
    await session.commit()

    assert await repo.close(sub.id, user_id=1) == "new"  # prev status returned
    await session.commit()
    assert await repo.close(sub.id, user_id=2) is None  # already closed
    await session.commit()

    refreshed = await session.get(Submission, sub.id, populate_existing=True)
    assert refreshed.status == SubmissionStatus.closed
    assert refreshed.closed_by_user_id == 1
    assert refreshed.closed_at is not None


@pytest.mark.asyncio
async def test_try_claim_missing_submission(session):
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    assert await repo.try_claim(999999, user_id=1) is False


@pytest.mark.asyncio
async def test_ticket_counters_independent_matrix(session):
    """Three (type, year) counters must advance independently when interleaved."""
    from bot.db.repositories import SubmissionRepository

    repo = SubmissionRepository(session)
    a1 = await repo.next_ticket_number(SubmissionType.appeal, year=2025)
    c1 = await repo.next_ticket_number(SubmissionType.corruption, year=2025)
    a2026 = await repo.next_ticket_number(SubmissionType.appeal, year=2026)
    a2 = await repo.next_ticket_number(SubmissionType.appeal, year=2025)
    await session.commit()
    assert a1 == "OBR-2025-0001"
    assert c1 == "COR-2025-0001"
    assert a2026 == "OBR-2026-0001"
    assert a2 == "OBR-2025-0002"


@pytest.mark.asyncio
async def test_audit_log(session):
    from sqlalchemy import select

    from bot.db.models import AuditLog
    from bot.db.repositories import AuditRepository

    await AuditRepository(session).log(action="assign_role", actor_user_id=1, target="user:5")
    await session.commit()
    rows = list(await session.scalars(select(AuditLog)))
    assert len(rows) == 1
    assert rows[0].action == "assign_role"


async def test_user_needs_at_least_one_identity(session):
    from sqlalchemy.exc import IntegrityError

    from bot.db.models import User

    session.add(User(full_name="nobody"))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(User(matrix_id="@nodir:example.uz", full_name="Nodir"))
    await session.flush()  # tg_id may be NULL when matrix_id is present
