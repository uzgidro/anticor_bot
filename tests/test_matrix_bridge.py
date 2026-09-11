"""The Matrix bridge: cards go to the right room, replies resolve to the
submission, members are provisioned as Users, and every action goes through
SubmissionActions (so type/ownership rules hold)."""
from io import BytesIO
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
    AttachmentType,
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
from bot.services.submissions import AttachmentInput, SubmissionInput, SubmissionService

APPEAL_ROOM = "!appeal:x"
CORRUPTION_ROOM = "!corr:x"
NODIR = "@nodir:x"

_core = SimpleNamespace(get=lambda key, locale=None, **kw: f"{key}|{kw}" if kw else key)


class FakeClient:
    def __init__(self):
        self.user_id = "@anticorbot:x"
        self.started_ms = 0
        self.sent = []       # (room, html, reply_to)
        self.edits = []      # (room, event_id, html)
        self.uploads = []    # (room, name, mime, size)
        self.reactions = []
        self.names = {NODIR: "Nodir"}
        self._n = 0

    def _eid(self):
        self._n += 1
        return f"$e{self._n}"

    async def send_html(self, room_id, html_text, *, reply_to=None):
        self.sent.append((room_id, html_text, reply_to))
        return self._eid()

    async def edit_html(self, room_id, event_id, html_text):
        self.edits.append((room_id, event_id, html_text))
        return True

    async def upload_file(self, room_id, data, name, mime):
        self.uploads.append((room_id, name, mime, len(data)))
        return self._eid()

    async def react(self, room_id, event_id, emoji):
        self.reactions.append((event_id, emoji))

    async def member_display_name(self, room_id, user_id):
        return self.names.get(user_id)


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)

    settings = MatrixSettings(
        homeserver="https://m.x", user="@anticorbot:x", password="pw",
        room_appeal=APPEAL_ROOM, room_corruption=CORRUPTION_ROOM, locale="uz_latn",
    )
    cipher = AnonCipher(Fernet.generate_key().decode())
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1)
    bot.download.side_effect = lambda *_: BytesIO(b"\xff\xd8jpegdata")  # fresh per call
    client = FakeClient()
    bridge = MatrixBridge(client, settings, pool, bot, _core, cipher, "ru")
    yield SimpleNamespace(bridge=bridge, client=client, pool=pool, bot=bot, cipher=cipher)
    await engine.dispose()


async def _submission(env, *, type_=SubmissionType.appeal, anonymous=False,
                      author_tg=900, attachments=0):
    async with env.pool() as s:
        author_uid = None
        if not anonymous:
            author = User(tg_id=author_tg, language="ru")
            s.add(author)
            await s.flush()
            author_uid = author.id
        svc = SubmissionService(s, env.cipher)
        sub = await svc.create(SubmissionInput(
            type=type_, text="report <x>", is_anonymous=anonymous,
            author_tg_id=author_tg, author_user_id=author_uid,
            attachments=[
                AttachmentInput(f"file{i}", AttachmentType.photo) for i in range(attachments)
            ],
        ))
        await s.commit()
        return sub.id


async def _announce(env, sub_id):
    async with env.pool() as s:
        ok = await env.bridge.announce(s, sub_id)
        await s.commit()
    return ok


async def _card_id(env, sub_id):
    async with env.pool() as s:
        row = await s.scalar(
            select(MatrixDelivery).where(
                MatrixDelivery.submission_id == sub_id, MatrixDelivery.kind == "card"
            )
        )
        return row.event_id


def _ev(room, body, reply_to="", sender=NODIR, ts=10_000, eid="$in"):
    return RoomEvent(room_id=room, sender=sender, event_id=eid, body=body,
                     reply_to=reply_to, server_ts=ts)


async def test_announce_posts_card_and_attachments_to_type_room(env):
    sub_id = await _submission(env, attachments=2)

    assert await _announce(env, sub_id) is True

    rooms = [room for room, _, _ in env.client.sent]
    assert rooms == [APPEAL_ROOM]
    assert "&lt;x&gt;" in env.client.sent[0][1]  # escaped user text
    assert [u[0] for u in env.client.uploads] == [APPEAL_ROOM, APPEAL_ROOM]
    async with env.pool() as s:
        kinds = sorted(r.kind for r in await s.scalars(select(MatrixDelivery)))
        assert kinds == ["attachment", "attachment", "card"]


async def test_corruption_goes_to_corruption_room(env):
    sub_id = await _submission(env, type_=SubmissionType.corruption, anonymous=True)
    await _announce(env, sub_id)
    assert env.client.sent[0][0] == CORRUPTION_ROOM
    assert "+998" not in env.client.sent[0][1]


async def test_announce_without_room_configured_returns_false(env):
    env.bridge.settings.room_appeal = ""
    sub_id = await _submission(env)
    assert await _announce(env, sub_id) is False
    assert env.client.sent == []


async def test_take_by_reply_provisions_user_and_claims(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card))

    async with env.pool() as s:
        user = await s.scalar(select(User).where(User.matrix_id == NODIR))
        assert user is not None and user.resp_appeal and not user.is_admin
        assert user.full_name == "Nodir"
        sub = await s.get(Submission, sub_id)
        assert sub.status == SubmissionStatus.in_progress
        assert sub.assigned_to_user_id == user.id
    assert env.client.reactions[-1] == ("$in", "👍")
    # Card redrawn with the assignee.
    assert env.client.edits and "Nodir" in env.client.edits[-1][2]


async def test_reply_to_attachment_also_resolves(env):
    sub_id = await _submission(env, attachments=1)
    await _announce(env, sub_id)
    async with env.pool() as s:
        att = await s.scalar(select(MatrixDelivery).where(MatrixDelivery.kind == "attachment"))
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=att.event_id))
    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.in_progress


async def test_text_reply_answers_applicant(env):
    sub_id = await _submission(env, author_tg=900)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "Salom, ko'rib chiqamiz", reply_to=card))

    async with env.pool() as s:
        resp = (await s.scalars(select(SubmissionResponse))).one()
        assert resp.text == "Salom, ko'rib chiqamiz"
    assert any(c.args[0] == 900 for c in env.bot.send_message.await_args_list)
    assert env.client.reactions[-1][1] == "👍"


async def test_close_by_owner_notifies_and_redraws(env):
    sub_id = await _submission(env, author_tg=900)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card))

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!yopish", reply_to=card))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.closed
    assert any(c.args[0] == 900 for c in env.bot.send_message.await_args_list)
    assert "mx-hint-card" in env.client.edits[-1][2]


async def test_non_owner_cannot_close(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card, sender=NODIR))

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!yopish", reply_to=card, sender="@other:x"))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.in_progress
    assert "mx-not-owner" in env.client.sent[-1][1]


async def test_wrong_room_type_is_rejected(env):
    """A corruption submission addressed by public id from the appeal room: the
    member gets resp_appeal from the room, but the type is re-derived from the
    row, so nothing happens. (Reply resolution is room-scoped anyway, so a
    reply to the corruption card cannot even reach here from another room.)"""
    sub_id = await _submission(env, type_=SubmissionType.corruption, anonymous=True)
    await _announce(env, sub_id)
    async with env.pool() as s:
        public_id = (await s.get(Submission, sub_id)).public_id

    await env.bridge.handle_event(_ev(APPEAL_ROOM, f"!olish {public_id}"))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.new
    assert "mx-forbidden" in env.client.sent[-1][1]


async def test_chatter_and_old_and_own_events_are_ignored(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)
    before = (len(env.client.sent), len(env.client.reactions))

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "tushlikka?", reply_to=""))
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to="$notours"))
    env.bridge.client.started_ms = 99_999
    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!olish", reply_to=card, ts=5))
    env.bridge.client.started_ms = 0
    await env.bridge.handle_event(
        _ev(APPEAL_ROOM, "!olish", reply_to=card, sender="@anticorbot:x")
    )
    await env.bridge.handle_event(_ev("!unknown:x", "!olish", reply_to=card))

    assert (len(env.client.sent), len(env.client.reactions)) == before
    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.new


async def test_help_and_card_commands(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    card = await _card_id(env, sub_id)

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!yordam"))
    assert "mx-help" in env.client.sent[-1][1]

    await env.bridge.handle_event(_ev(APPEAL_ROOM, "!karta", reply_to=card))
    assert "mx-hint-take" in env.client.sent[-1][1]


async def test_refresh_from_telegram_side_edits_card(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    async with env.pool() as s:
        sub = await s.get(Submission, sub_id)
        sub.status = SubmissionStatus.closed
        await s.commit()
        await env.bridge.refresh(s, sub_id)
    assert env.client.edits and "mx-hint-card" in env.client.edits[-1][2]
