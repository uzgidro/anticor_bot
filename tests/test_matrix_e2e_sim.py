"""Whole-story simulation with fakes: nothing here touches Telegram or Matrix.

Applicant (Telegram) -> Telegram push cards + DM cards -> take from Element ->
Telegram cards redrawn -> reply from Element reaches the applicant -> close from
Telegram -> DM cards redrawn.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import MatrixSettings
from bot.db.base import Base
from bot.db.models import (
    MatrixDelivery,
    Submission,
    SubmissionResponse,
    SubmissionStatus,
    SubmissionType,
    User,
)
from bot.matrix.bridge import MatrixBridge
from bot.matrix.client import RoomEvent
from bot.security.crypto import AnonCipher
from bot.services.actions import SubmissionActions
from bot.services.submissions import SubmissionInput, SubmissionService
from tests.test_matrix_bridge import FakeClient, _core

UGE, UGE_DM = "@uge132:gidro.uz", "!uge-dm:gidro.uz"
APPLICANT_TG, OFFICER_TG = 900, 1160136690


@pytest_asyncio.fixture
async def world():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        s.add_all([
            User(tg_id=OFFICER_TG, username="bakhodirovxz", is_admin=True, resp_appeal=True),
            User(matrix_id=UGE, matrix_room_id=UGE_DM, full_name="Uge", resp_appeal=True),
            User(tg_id=APPLICANT_TG, language="uz_latn"),
        ])
        await s.commit()
    settings = MatrixSettings(homeserver="https://m.x", user="@anticorbot:x", password="pw")
    cipher = AnonCipher(Fernet.generate_key().decode())
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=42)
    client = FakeClient()
    client.names[UGE] = "Uge"
    bridge = MatrixBridge(client, settings, pool, bot, _core, cipher, "ru")
    yield SimpleNamespace(pool=pool, bot=bot, client=client, bridge=bridge, cipher=cipher)
    await engine.dispose()


async def test_full_story(world):
    # 1. Applicant submits in Telegram (what handlers/submission.py does after commit).
    async with world.pool() as s:
        svc = SubmissionService(s, world.cipher)
        applicant = await s.scalar(select(User).where(User.tg_id == APPLICANT_TG))
        sub = await svc.create(SubmissionInput(
            type=SubmissionType.appeal, text="Suv yo'q", is_anonymous=False,
            author_tg_id=APPLICANT_TG, author_user_id=applicant.id, attachments=[],
        ))
        await s.commit()
        assert await svc.dispatch_to_responsibles(world.bot, _core, sub, "ru") == 1
        assert await world.bridge.announce(s, sub.id) is True
        await s.commit()
        sub_id = sub.id
    # Telegram officer got a push card; Uge got the DM card.
    assert [c.args[0] for c in world.bot.send_message.await_args_list] == [OFFICER_TG]
    assert [r for r, _, _ in world.client.sent] == [UGE_DM]
    async with world.pool() as s:
        card_event = (await s.scalar(select(MatrixDelivery).where(
            MatrixDelivery.submission_id == sub_id, MatrixDelivery.kind == "card"
        ))).event_id

    # 2. Uge takes it from Element.
    await world.bridge.handle_event(RoomEvent(
        room_id=UGE_DM, sender=UGE, event_id="$take", body="!olish",
        reply_to=card_event, server_ts=10_000,
    ))
    async with world.pool() as s:
        sub = await s.get(Submission, sub_id)
        uge = await s.scalar(select(User).where(User.matrix_id == UGE))
        assert sub.status == SubmissionStatus.in_progress
        assert sub.assigned_to_user_id == uge.id
    # Telegram officer's card redrawn (update_all_cards -> edit_message_text) ...
    assert world.bot.edit_message_text.await_count >= 1
    # ... and the DM card too.
    assert any("Uge" in html for _, _, html in world.client.edits)

    # 3. Uge replies from Element -> the applicant gets it in Telegram.
    await world.bridge.handle_event(RoomEvent(
        room_id=UGE_DM, sender=UGE, event_id="$reply", body="Brigada yuborildi",
        reply_to=card_event, server_ts=10_001,
    ))
    to_applicant = [
        c for c in world.bot.send_message.await_args_list if c.args[0] == APPLICANT_TG
    ]
    assert to_applicant and "Brigada yuborildi" in to_applicant[-1].args[1]
    async with world.pool() as s:
        assert (await s.scalars(select(SubmissionResponse))).one().text == "Brigada yuborildi"

    # 4. Admin closes from Telegram (what handlers/responsible.py does).
    world.client.edits.clear()
    async with world.pool() as s:
        officer = await s.scalar(select(User).where(User.tg_id == OFFICER_TG))
        actions = SubmissionActions(
            s, world.bot, _core, world.cipher, "ru", sinks=[world.bridge]
        )
        sub = await actions.authorize(sub_id, officer, require_owner=True)  # admin may
        assert sub is not None
        assert await actions.close(sub, officer) is True
    async with world.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.closed
    assert any("mx-hint-card" in html for _, _, html in world.client.edits)  # DM redrawn
    assert any(
        c.args[0] == APPLICANT_TG for c in world.bot.send_message.await_args_list[-2:]
    )  # applicant told it's closed


async def test_new_staff_joins_via_dm_then_assign_then_receives(world):
    """The onboarding path from the README: write to the bot, admin assigns,
    next submission arrives in that same DM."""
    await world.bridge.on_direct_room("!dm-new:gidro.uz", "@yangi:gidro.uz")
    assert world.client.sent[-1][0] == "!dm-new:gidro.uz"
    assert "mx-dm-welcome" in world.client.sent[-1][1]

    # /assign @yangi:gidro.uz -> corruption (what handlers/admin.py does).
    async with world.pool() as s:
        yangi = await s.scalar(select(User).where(User.matrix_id == "@yangi:gidro.uz"))
        assert yangi.matrix_room_id == "!dm-new:gidro.uz"
        assert yangi.resp_corruption is False
        yangi.resp_corruption = True
        await s.commit()

    world.client.sent.clear()
    async with world.pool() as s:
        svc = SubmissionService(s, world.cipher)
        sub = await svc.create(SubmissionInput(
            type=SubmissionType.corruption, text="pora", is_anonymous=True,
            author_tg_id=APPLICANT_TG, author_user_id=None, attachments=[],
        ))
        await s.commit()
        assert await world.bridge.announce(s, sub.id) is True
        await s.commit()
    # Only the corruption responsible's DM — Uge (appeal) gets nothing.
    assert [r for r, _, _ in world.client.sent] == ["!dm-new:gidro.uz"]
    assert world.client.created == []  # existing DM reused, none created
