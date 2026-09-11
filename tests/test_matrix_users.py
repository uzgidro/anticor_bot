"""Matrix-only users: provisioned on first action, one per Matrix id, role from
the room type, never a Telegram recipient.
"""
from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.db.repositories import MatrixDeliveryRepository, UserRepository


async def test_first_action_creates_user_with_room_role(session):
    repo = UserRepository(session)
    user, created = await repo.get_or_create_matrix(
        "@nodir:example.uz", "Nodir", SubmissionType.appeal
    )
    assert created is True
    assert user.matrix_id == "@nodir:example.uz"
    assert user.tg_id is None
    assert user.full_name == "Nodir"
    assert user.resp_appeal is True
    assert user.resp_corruption is False
    assert user.is_admin is False


async def test_second_action_reuses_user(session):
    repo = UserRepository(session)
    first, _ = await repo.get_or_create_matrix("@nodir:example.uz", "Nodir", SubmissionType.appeal)
    second, created = await repo.get_or_create_matrix(
        "@nodir:example.uz", "Nodir N.", SubmissionType.appeal
    )
    assert created is False
    assert second.id == first.id


async def test_member_of_both_rooms_gets_both_flags(session):
    repo = UserRepository(session)
    await repo.get_or_create_matrix("@nodir:example.uz", "Nodir", SubmissionType.appeal)
    user, _ = await repo.get_or_create_matrix(
        "@nodir:example.uz", "Nodir", SubmissionType.corruption
    )
    assert user.resp_appeal is True and user.resp_corruption is True


async def test_matrix_users_are_not_telegram_recipients(session):
    repo = UserRepository(session)
    await repo.get_or_create_matrix("@nodir:example.uz", "Nodir", SubmissionType.appeal)
    tg = User(tg_id=100, resp_appeal=True)
    session.add(tg)
    await session.flush()

    recipients = await repo.responsibles_for(SubmissionType.appeal)
    assert [u.tg_id for u in recipients] == [100]


async def test_matrix_deliveries_resolve_replies(session):
    sub = Submission(
        public_id="ABCDEFGH", ticket_number="OBR-2026-0001", type=SubmissionType.appeal,
        status=SubmissionStatus.new, text="t",
    )
    session.add(sub)
    await session.flush()

    repo = MatrixDeliveryRepository(session)
    card = await repo.add(sub.id, "!room:x", "$card", "card")
    await repo.add(sub.id, "!room:x", "$att1", "attachment")

    assert await repo.submission_id_for("!room:x", "$card") == sub.id
    assert await repo.submission_id_for("!room:x", "$att1") == sub.id
    assert await repo.submission_id_for("!room:x", "$unknown") is None
    assert (await repo.card_for(sub.id)).event_id == card.event_id
