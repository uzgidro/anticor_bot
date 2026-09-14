"""The Matrix bridge in DM mode: every responsible with a Matrix id gets the card
in their private room with the bot; only the room's owner may act from it; every
action goes through SubmissionActions (type/ownership rules hold); status changes
redraw every DM card and the Telegram cards."""
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
from bot.services.actions import SubmissionActions
from bot.services.submissions import AttachmentInput, SubmissionInput, SubmissionService

NODIR, NODIR_DM = "@nodir:x", "!nodir-dm:x"
OLIM, OLIM_DM = "@olim:x", "!olim-dm:x"
KARIM = "@karim:x"  # corruption-only, no DM yet

_core = SimpleNamespace(get=lambda key, locale=None, **kw: f"{key}|{kw}" if kw else key)


class FakeClient:
    def __init__(self):
        self.user_id = "@anticorbot:x"
        self.started_ms = 0
        self.sent = []       # (room, html, reply_to)
        self.edits = []      # (room, event_id, html)
        self.uploads = []    # (room, name, mime, size)
        self.reactions = []
        self.created = []    # user ids create_dm was called for
        self.fail_dm_for = set()
        self.names = {NODIR: "Nodir", OLIM: "Olim"}
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

    async def create_dm(self, user_id):
        self.created.append(user_id)
        if user_id in self.fail_dm_for:
            return ""
        return f"!dm-{user_id.lstrip('@').split(':')[0]}:x"


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
        # Two appeal responsibles with DMs, one corruption responsible without.
        s.add_all([
            User(matrix_id=NODIR, matrix_room_id=NODIR_DM, full_name="Nodir", resp_appeal=True),
            User(matrix_id=OLIM, matrix_room_id=OLIM_DM, full_name="Olim", resp_appeal=True),
            User(matrix_id=KARIM, full_name="Karim", resp_corruption=True),
        ])
        await s.commit()

    settings = MatrixSettings(
        homeserver="https://m.x", user="@anticorbot:x", password="pw", locale="uz_latn",
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


async def _cards(env, sub_id):
    """{room_id: card event_id} for the submission."""
    async with env.pool() as s:
        rows = await s.scalars(select(MatrixDelivery).where(
            MatrixDelivery.submission_id == sub_id, MatrixDelivery.kind == "card"
        ))
        return {r.room_id: r.event_id for r in rows}


def _ev(room, body, reply_to="", sender=NODIR, ts=10_000, eid="$in"):
    return RoomEvent(room_id=room, sender=sender, event_id=eid, body=body,
                     reply_to=reply_to, server_ts=ts)


# --- outbound ---------------------------------------------------------------

async def test_announce_fans_out_to_every_responsible_dm(env):
    sub_id = await _submission(env, attachments=2)

    assert await _announce(env, sub_id) is True

    rooms = [room for room, _, _ in env.client.sent]
    assert rooms == [NODIR_DM, OLIM_DM]
    assert all("&lt;x&gt;" in html for _, html, _ in env.client.sent)  # escaped user text
    assert [u[0] for u in env.client.uploads] == [NODIR_DM, NODIR_DM, OLIM_DM, OLIM_DM]
    assert set((await _cards(env, sub_id)).keys()) == {NODIR_DM, OLIM_DM}
    async with env.pool() as s:
        kinds = sorted(r.kind for r in await s.scalars(select(MatrixDelivery)))
        assert kinds == ["attachment"] * 4 + ["card"] * 2


async def test_announce_creates_dm_for_responsible_without_room(env):
    sub_id = await _submission(env, type_=SubmissionType.corruption, anonymous=True)

    assert await _announce(env, sub_id) is True

    assert env.client.created == [KARIM]
    assert [room for room, _, _ in env.client.sent] == ["!dm-karim:x"]
    assert "+998" not in env.client.sent[0][1]
    async with env.pool() as s:
        karim = await s.scalar(select(User).where(User.matrix_id == KARIM))
        assert karim.matrix_room_id == "!dm-karim:x"


async def test_dm_creation_failure_skips_only_that_recipient(env):
    async with env.pool() as s:
        s.add(User(matrix_id="@new:x", resp_appeal=True))
        await s.commit()
    env.client.fail_dm_for.add("@new:x")
    sub_id = await _submission(env)

    assert await _announce(env, sub_id) is True

    assert [room for room, _, _ in env.client.sent] == [NODIR_DM, OLIM_DM]
    async with env.pool() as s:
        new = await s.scalar(select(User).where(User.matrix_id == "@new:x"))
        assert new.matrix_room_id is None


async def test_announce_without_matrix_responsibles_returns_false(env):
    async with env.pool() as s:
        for u in await s.scalars(select(User).where(User.matrix_id.is_not(None))):
            u.resp_appeal = False
        await s.commit()
    sub_id = await _submission(env)
    assert await _announce(env, sub_id) is False
    assert env.client.sent == []


async def test_refresh_edits_every_card(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    async with env.pool() as s:
        sub = await s.get(Submission, sub_id)
        sub.status = SubmissionStatus.closed
        await s.commit()
        await env.bridge.refresh(s, sub_id)
    edited = {(room, eid) for room, eid, _ in env.client.edits}
    assert edited == set((await _cards(env, sub_id)).items())
    assert all("mx-hint-card" in html for _, _, html in env.client.edits)


# --- inbound ----------------------------------------------------------------

async def test_take_by_reply_claims_and_redraws_everywhere(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)

    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM]))

    async with env.pool() as s:
        user = await s.scalar(select(User).where(User.matrix_id == NODIR))
        sub = await s.get(Submission, sub_id)
        assert sub.status == SubmissionStatus.in_progress
        assert sub.assigned_to_user_id == user.id
    assert env.client.reactions[-1] == ("$in", "👍")
    # Both DM cards redrawn with the assignee.
    edited = {room for room, _, html in env.client.edits if "Nodir" in html}
    assert edited == {NODIR_DM, OLIM_DM}


async def test_second_taker_is_told_who_has_it(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM]))

    await env.bridge.handle_event(_ev(OLIM_DM, "!olish", reply_to=cards[OLIM_DM], sender=OLIM))

    assert "cb-already-taken" in env.client.sent[-1][1] and "Nodir" in env.client.sent[-1][1]
    async with env.pool() as s:
        nodir = await s.scalar(select(User).where(User.matrix_id == NODIR))
        assert (await s.get(Submission, sub_id)).assigned_to_user_id == nodir.id


async def test_reply_to_attachment_also_resolves(env):
    sub_id = await _submission(env, attachments=1)
    await _announce(env, sub_id)
    async with env.pool() as s:
        att = await s.scalar(select(MatrixDelivery).where(
            MatrixDelivery.kind == "attachment", MatrixDelivery.room_id == NODIR_DM
        ))
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=att.event_id))
    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.in_progress


async def test_text_reply_reaches_applicant_in_telegram(env):
    sub_id = await _submission(env, author_tg=900)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM]))

    await env.bridge.handle_event(
        _ev(NODIR_DM, "Salom, ko'rib chiqamiz", reply_to=cards[NODIR_DM])
    )

    async with env.pool() as s:
        resp = (await s.scalars(select(SubmissionResponse))).one()
        assert resp.text == "Salom, ko'rib chiqamiz"
    to_author = [c for c in env.bot.send_message.await_args_list if c.args[0] == 900]
    assert to_author and "Salom" in to_author[-1].args[1]
    assert env.client.reactions[-1][1] == "👍"


async def test_text_reply_to_anonymous_applicant_is_decrypted(env):
    sub_id = await _submission(env, anonymous=True, author_tg=777)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM]))

    await env.bridge.handle_event(_ev(NODIR_DM, "Qabul qilindi", reply_to=cards[NODIR_DM]))

    assert any(c.args[0] == 777 for c in env.bot.send_message.await_args_list)


async def test_close_by_owner_notifies_and_redraws(env):
    sub_id = await _submission(env, author_tg=900)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM]))

    await env.bridge.handle_event(_ev(NODIR_DM, "!yopish", reply_to=cards[NODIR_DM]))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.closed
    assert any(c.args[0] == 900 for c in env.bot.send_message.await_args_list)
    closed_rooms = {room for room, _, html in env.client.edits if "mx-hint-card" in html}
    assert closed_rooms == {NODIR_DM, OLIM_DM}


async def test_non_owner_cannot_close_or_reply(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM]))

    await env.bridge.handle_event(_ev(OLIM_DM, "!yopish", reply_to=cards[OLIM_DM], sender=OLIM))
    assert "mx-not-owner" in env.client.sent[-1][1]
    await env.bridge.handle_event(_ev(OLIM_DM, "javob", reply_to=cards[OLIM_DM], sender=OLIM))
    assert "mx-not-owner" in env.client.sent[-1][1]

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.in_progress
        assert (await s.scalars(select(SubmissionResponse))).all() == []


async def test_wrong_type_is_forbidden_and_grants_nothing(env):
    """An appeal responsible names a corruption submission by public id from his
    own DM: the type comes from the row, so authorize() refuses, and — unlike
    the old room mode — nothing is provisioned."""
    sub_id = await _submission(env, type_=SubmissionType.corruption, anonymous=True)
    await _announce(env, sub_id)
    async with env.pool() as s:
        public_id = (await s.get(Submission, sub_id)).public_id

    await env.bridge.handle_event(_ev(NODIR_DM, f"!olish {public_id}"))

    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.new
        nodir = await s.scalar(select(User).where(User.matrix_id == NODIR))
        assert nodir.resp_corruption is False
    assert "mx-forbidden" in env.client.sent[-1][1]


async def test_foreign_sender_unknown_room_old_and_own_events_are_ignored(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)
    before = (len(env.client.sent), len(env.client.reactions))

    # Olim writing inside Nodir's DM (impossible in a real 1:1, but never trust it).
    await env.bridge.handle_event(
        _ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM], sender=OLIM)
    )
    await env.bridge.handle_event(_ev("!unknown:x", "!olish", reply_to=cards[NODIR_DM]))
    await env.bridge.handle_event(_ev(NODIR_DM, "tushlikka?", reply_to=""))
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to="$notours"))
    env.bridge.client.started_ms = 99_999
    await env.bridge.handle_event(_ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM], ts=5))
    env.bridge.client.started_ms = 0
    await env.bridge.handle_event(
        _ev(NODIR_DM, "!olish", reply_to=cards[NODIR_DM], sender="@anticorbot:x")
    )

    assert (len(env.client.sent), len(env.client.reactions)) == before
    async with env.pool() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.new


async def test_help_and_card_commands(env):
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    cards = await _cards(env, sub_id)

    await env.bridge.handle_event(_ev(NODIR_DM, "!yordam"))
    assert env.client.sent[-1][0] == NODIR_DM and "mx-help" in env.client.sent[-1][1]

    await env.bridge.handle_event(_ev(NODIR_DM, "!karta", reply_to=cards[NODIR_DM]))
    assert env.client.sent[-1][0] == NODIR_DM and "mx-hint-take" in env.client.sent[-1][1]


async def test_help_in_unbound_room_is_ignored(env):
    await env.bridge.handle_event(_ev("!stranger:x", "!yordam", sender="@stranger:x"))
    assert env.client.sent == []


# --- DM binding -------------------------------------------------------------

async def test_on_direct_room_binds_new_user_and_welcomes(env):
    await env.bridge.on_direct_room("!dm-new:x", "@new:x")

    async with env.pool() as s:
        user = await s.scalar(select(User).where(User.matrix_id == "@new:x"))
        assert user.matrix_room_id == "!dm-new:x"
        assert (user.resp_appeal, user.resp_corruption, user.is_admin) == (False, False, False)
    room, html, _ = env.client.sent[-1]
    assert room == "!dm-new:x" and html.startswith("mx-dm-welcome") and "@new:x" in html


async def test_on_direct_room_keeps_existing_binding(env):
    await env.bridge.on_direct_room("!second:x", NODIR)
    async with env.pool() as s:
        nodir = await s.scalar(select(User).where(User.matrix_id == NODIR))
        assert nodir.matrix_room_id == NODIR_DM
    assert env.client.sent[-1][0] == "!second:x"  # still greeted, in the new room


# --- cross-channel ----------------------------------------------------------

async def test_take_from_telegram_side_redraws_dm_cards(env):
    """SubmissionActions with the bridge as a sink — what handlers/responsible.py
    does — must redraw every DM card."""
    sub_id = await _submission(env)
    await _announce(env, sub_id)
    async with env.pool() as s:
        officer = User(tg_id=500, full_name="Officer", resp_appeal=True)
        s.add(officer)
        await s.flush()
        actions = SubmissionActions(s, env.bot, _core, env.cipher, "ru", sinks=[env.bridge])
        sub = await actions.authorize(sub_id, officer)
        result = await actions.take(sub, officer)
        assert result.won
    edited = {room for room, _, html in env.client.edits if "Officer" in html}
    assert edited == {NODIR_DM, OLIM_DM}
