"""Tests for SubmissionService — focus on anonymity enforcement (no PII)."""
import pytest
from cryptography.fernet import Fernet

from bot.db.models import AttachmentType, Submission, SubmissionType
from bot.security.crypto import AnonCipher


@pytest.fixture
def cipher() -> AnonCipher:
    return AnonCipher(Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_create_non_anonymous_keeps_pii(session, cipher):
    from bot.db.repositories import UserRepository
    from bot.services.submissions import SubmissionInput, SubmissionService

    author, _ = await UserRepository(session).get_or_create(tg_id=10)
    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.appeal, text="hello", is_anonymous=False,
            author_tg_id=10, author_user_id=author.id, full_name="Ivan", phone="+998901112233",
        )
    )
    await session.commit()
    assert sub.full_name == "Ivan"
    assert sub.phone == "+998901112233"
    assert sub.author_user_id == author.id
    assert sub.public_id and sub.ticket_number.startswith("OBR-")


@pytest.mark.asyncio
async def test_create_anonymous_strips_all_pii(session, cipher):
    """The core anonymity invariant: anonymous submission stores NO PII."""
    from bot.services.submissions import SubmissionInput, SubmissionService

    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.corruption, text="bribe report", is_anonymous=True,
            author_tg_id=777,
            # Even if the caller passes PII, it must NOT be persisted.
            author_user_id=999, full_name="Should Not Persist", phone="+998000000000",
        )
    )
    await session.commit()
    assert sub.is_anonymous is True
    assert sub.author_user_id is None
    assert sub.full_name is None
    assert sub.phone is None
    assert sub.ticket_number.startswith("COR-")


@pytest.mark.asyncio
async def test_anonymous_chat_ref_is_encrypted_and_recoverable(session, cipher):
    from bot.db.repositories import SubmissionRepository
    from bot.services.submissions import SubmissionInput, SubmissionService

    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.corruption, text="x", is_anonymous=True, author_tg_id=424242,
        )
    )
    await session.commit()

    token = await SubmissionRepository(session).get_anon_ref(sub.id)
    assert token is not None
    assert b"424242" not in token  # not stored in clear
    # Service can recover the chat id for delivery (decrypt in memory).
    assert await svc.resolve_author_chat_id(sub) == 424242


@pytest.mark.asyncio
async def test_resolve_author_chat_id_non_anonymous(session, cipher):
    from bot.db.repositories import UserRepository
    from bot.services.submissions import SubmissionInput, SubmissionService

    author, _ = await UserRepository(session).get_or_create(tg_id=55)
    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.appeal, text="t", is_anonymous=False,
            author_tg_id=55, author_user_id=author.id,
        )
    )
    await session.commit()
    assert await svc.resolve_author_chat_id(sub) == 55


@pytest.mark.asyncio
async def test_attachments_persisted_in_order(session, cipher):
    from sqlalchemy import select

    from bot.db.models import SubmissionAttachment
    from bot.services.submissions import AttachmentInput, SubmissionInput, SubmissionService

    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.appeal, text="t", is_anonymous=False, author_tg_id=1,
            attachments=[
                AttachmentInput("f1", AttachmentType.photo),
                AttachmentInput("f2", AttachmentType.document),
            ],
        )
    )
    await session.commit()
    atts = list(
        await session.scalars(
            select(SubmissionAttachment).where(SubmissionAttachment.submission_id == sub.id)
        )
    )
    assert [a.file_id for a in sorted(atts, key=lambda a: a.order)] == ["f1", "f2"]


_LOCALES_PATH = str(
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "bot" / "locales" / "{locale}" / "LC_MESSAGES"
)


@pytest.mark.asyncio
async def test_render_card_anonymous_has_no_pii():
    from aiogram_i18n.cores.fluent_runtime_core import FluentRuntimeCore

    from bot.services.submissions import render_card

    core = FluentRuntimeCore(path=_LOCALES_PATH)
    await core.startup()

    sub = Submission(
        id=1, public_id="ABC", ticket_number="COR-2026-0001",
        type=SubmissionType.corruption, is_anonymous=True, text="secret report",
        full_name="LEAK NAME", phone="+998999999999",
    )
    card = render_card(core, "ru", sub, type_label="Жалоба")
    assert "LEAK NAME" not in card
    assert "+998999999999" not in card
    assert "secret report" in card  # body is shown


@pytest.mark.asyncio
async def test_my_submissions_excludes_anonymous(session, cipher):
    """The 'my submissions' query must never surface anonymous complaints."""
    from sqlalchemy import select

    from bot.db.repositories import UserRepository
    from bot.services.submissions import SubmissionInput, SubmissionService

    author, _ = await UserRepository(session).get_or_create(tg_id=70)
    svc = SubmissionService(session, cipher)
    await svc.create(SubmissionInput(
        type=SubmissionType.appeal, text="visible", is_anonymous=False,
        author_tg_id=70, author_user_id=author.id,
    ))
    await svc.create(SubmissionInput(
        type=SubmissionType.corruption, text="hidden", is_anonymous=True, author_tg_id=70,
    ))
    await session.commit()

    visible = list(await session.scalars(
        select(Submission).where(
            Submission.author_user_id == author.id,
            Submission.is_anonymous.is_(False),
        )
    ))
    assert len(visible) == 1
    assert visible[0].text == "visible"
