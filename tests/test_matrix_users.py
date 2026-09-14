"""Matrix users: created by /assign or by writing to the bot, never given a role
by a room; one DM room per user; Telegram fan-out never targets them.
"""
from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.db.repositories import MatrixDeliveryRepository, UserRepository


async def test_get_or_create_by_matrix_id_has_no_roles(session):
    repo = UserRepository(session)
    user, created = await repo.get_or_create_by_matrix_id("@nodir:example.uz", "Nodir")
    assert created is True
    assert user.matrix_id == "@nodir:example.uz"
    assert user.tg_id is None and user.matrix_room_id is None
    assert user.full_name == "Nodir"
    assert (user.resp_appeal, user.resp_corruption, user.is_admin) == (False, False, False)


async def test_get_or_create_by_matrix_id_reuses_and_keeps_name(session):
    repo = UserRepository(session)
    first, _ = await repo.get_or_create_by_matrix_id("@nodir:example.uz", "Nodir")
    second, created = await repo.get_or_create_by_matrix_id("@nodir:example.uz", "Nodir N.")
    assert created is False
    assert second.id == first.id
    assert second.full_name == "Nodir"  # first sight wins, like get_or_create for tg


async def test_bind_matrix_room_and_lookup(session):
    repo = UserRepository(session)
    user, _ = await repo.get_or_create_by_matrix_id("@nodir:example.uz")
    await repo.bind_matrix_room(user, "!dm1:x")
    await repo.bind_matrix_room(user, "!dm2:x")  # already bound -> unchanged
    assert user.matrix_room_id == "!dm1:x"
    assert (await repo.by_matrix_room("!dm1:x")).id == user.id
    assert await repo.by_matrix_room("!dm2:x") is None


async def test_matrix_responsibles_for_filters_by_type_and_identity(session):
    session.add_all([
        User(matrix_id="@a:x", resp_appeal=True),
        User(matrix_id="@c:x", resp_corruption=True),
        User(matrix_id="@none:x"),
        User(tg_id=100, resp_appeal=True),  # Telegram-only: not a Matrix recipient
    ])
    await session.flush()
    repo = UserRepository(session)
    appeal = await repo.matrix_responsibles_for(SubmissionType.appeal)
    corr = await repo.matrix_responsibles_for(SubmissionType.corruption)
    assert [u.matrix_id for u in appeal] == ["@a:x"]
    assert [u.matrix_id for u in corr] == ["@c:x"]


async def test_matrix_users_are_not_telegram_recipients(session):
    repo = UserRepository(session)
    session.add_all([User(matrix_id="@a:x", resp_appeal=True), User(tg_id=100, resp_appeal=True)])
    await session.flush()
    recipients = await repo.responsibles_for(SubmissionType.appeal)
    assert [u.tg_id for u in recipients] == [100]


async def test_matrix_deliveries_resolve_replies_and_list_cards(session):
    sub = Submission(
        public_id="ABCDEFGH", ticket_number="OBR-2026-0001", type=SubmissionType.appeal,
        status=SubmissionStatus.new, text="t",
    )
    session.add(sub)
    await session.flush()

    repo = MatrixDeliveryRepository(session)
    c1 = await repo.add(sub.id, "!dm1:x", "$card1", "card")
    await repo.add(sub.id, "!dm1:x", "$att1", "attachment")
    c2 = await repo.add(sub.id, "!dm2:x", "$card2", "card")

    assert await repo.submission_id_for("!dm1:x", "$card1") == sub.id
    assert await repo.submission_id_for("!dm1:x", "$att1") == sub.id
    assert await repo.submission_id_for("!dm2:x", "$card1") is None  # room-scoped
    assert [c.event_id for c in await repo.cards_for(sub.id)] == [c1.event_id, c2.event_id]
