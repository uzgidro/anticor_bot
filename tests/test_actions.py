"""SubmissionActions: the single implementation of take / close / reply used by
both Telegram handlers and the Matrix bridge.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from bot.db.models import (
    AuditLog,
    Submission,
    SubmissionResponse,
    SubmissionStatus,
    SubmissionType,
    User,
)
from bot.security.crypto import AnonCipher
from bot.services.actions import SubmissionActions, TakeResult
from bot.services.submissions import SubmissionInput, SubmissionService

_core = SimpleNamespace(get=lambda key, locale=None, **kw: key)


class _Sink:
    def __init__(self):
        self.refreshed = []
        self.announced = []

    async def announce(self, session, submission_id):
        self.announced.append(submission_id)
        return True

    async def refresh(self, session, submission_id):
        self.refreshed.append(submission_id)


async def _officer(session, *, tg_id=500, admin=False):
    user = User(tg_id=tg_id, full_name="Officer", is_admin=admin, resp_appeal=True)
    session.add(user)
    await session.flush()
    return user


async def _submission(session, cipher, *, anonymous=False, author_tg=900):
    author_uid = None
    if not anonymous:
        author = User(tg_id=author_tg, language="ru")
        session.add(author)
        await session.flush()
        author_uid = author.id
    svc = SubmissionService(session, cipher)
    sub = await svc.create(
        SubmissionInput(
            type=SubmissionType.corruption if anonymous else SubmissionType.appeal,
            text="report", is_anonymous=anonymous,
            author_tg_id=author_tg, author_user_id=author_uid,
        )
    )
    await session.commit()
    return sub


@pytest.fixture
def cipher():
    return AnonCipher(Fernet.generate_key().decode())


@pytest.fixture
def bot():
    b = AsyncMock()
    b.send_message.return_value = SimpleNamespace(message_id=1)
    return b


async def test_take_wins_records_and_refreshes(session, cipher, bot):
    officer = await _officer(session)
    sub = await _submission(session, cipher)
    sink = _Sink()
    actions = SubmissionActions(session, bot, _core, cipher, "ru", sinks=[sink])

    result = await actions.take(sub, officer)

    assert result == TakeResult(won=True, assignee_name="Officer")
    fresh = await session.get(Submission, sub.id, populate_existing=True)
    assert fresh.status == SubmissionStatus.in_progress
    assert fresh.assigned_to_user_id == officer.id
    audits = list(await session.scalars(select(AuditLog).where(AuditLog.action == "status_change")))
    assert any(a.meta == "new->in_progress" and a.actor_user_id == officer.id for a in audits)
    assert sink.refreshed == [sub.id]


async def test_take_loses_reports_current_assignee(session, cipher, bot):
    officer = await _officer(session)
    other = User(tg_id=601, full_name="Other", resp_appeal=True)
    session.add(other)
    await session.flush()
    sub = await _submission(session, cipher)
    actions = SubmissionActions(session, bot, _core, cipher, "ru")
    assert (await actions.take(sub, other)).won is True

    result = await actions.take(sub, officer)

    assert result.won is False
    assert result.assignee_name == "Other"


async def test_authorize_enforces_type_and_ownership(session, cipher, bot):
    officer = await _officer(session)  # resp_appeal only
    other = User(tg_id=601, resp_appeal=True)
    session.add(other)
    await session.flush()
    sub = await _submission(session, cipher)
    actions = SubmissionActions(session, bot, _core, cipher, "ru")

    assert (await actions.authorize(sub.id, officer)) is not None
    await actions.take(sub, other)
    # Not the assignee -> may not reply/close.
    assert (await actions.authorize(sub.id, officer, require_owner=True)) is None
    # Admin may.
    officer.is_admin = True
    assert (await actions.authorize(sub.id, officer, require_owner=True)) is not None

    anon = await _submission(session, cipher, anonymous=True, author_tg=4242)
    officer.is_admin = False
    assert (await actions.authorize(anon.id, officer)) is None  # no resp_corruption


async def test_close_notifies_author_and_refreshes(session, cipher, bot):
    officer = await _officer(session)
    sub = await _submission(session, cipher, author_tg=900)
    sink = _Sink()
    actions = SubmissionActions(session, bot, _core, cipher, "ru", sinks=[sink])

    assert await actions.close(sub, officer) is True
    assert await actions.close(sub, officer) is False  # already closed

    fresh = await session.get(Submission, sub.id, populate_existing=True)
    assert fresh.status == SubmissionStatus.closed
    assert any(c.args[0] == 900 for c in bot.send_message.await_args_list)
    assert sink.refreshed == [sub.id]


async def test_reply_reaches_anonymous_author(session, cipher, bot):
    officer = await _officer(session)
    officer.resp_corruption = True
    sub = await _submission(session, cipher, anonymous=True, author_tg=4242)
    actions = SubmissionActions(session, bot, _core, cipher, "ru")

    assert await actions.reply(sub, officer, "We are on it.") is True

    resp = (await session.scalars(select(SubmissionResponse))).one()
    assert resp.text == "We are on it." and resp.responder_user_id == officer.id
    assert any(c.args[0] == 4242 for c in bot.send_message.await_args_list)


async def test_reply_returns_false_when_author_blocked_bot(session, cipher, bot):
    from aiogram.exceptions import TelegramForbiddenError

    officer = await _officer(session)
    sub = await _submission(session, cipher, author_tg=900)
    bot.send_message.side_effect = TelegramForbiddenError(method=None, message="blocked")
    actions = SubmissionActions(session, bot, _core, cipher, "ru")

    assert await actions.reply(sub, officer, "hello") is False
    # The response is still recorded — the operator's work is not lost.
    assert (await session.scalars(select(SubmissionResponse))).one().text == "hello"
