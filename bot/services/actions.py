"""Submission actions — the ONE place take / close / reply are implemented.

Telegram handlers (handlers/responsible.py) and the Matrix bridge
(matrix/bridge.py) are adapters over this class. Authorization is derived
server-side from the acting User (never from client-supplied data), status
changes are atomic (repository), and every operation commits its rows BEFORE
any network fan-out so a responsible never learns about state a later
rollback would erase.

Card sinks: other channels that show the submission (today: the Matrix room)
register a CardSink so a status change in one channel is redrawn in all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram_i18n.cores import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Submission, SubmissionResponse, User
from bot.db.repositories import AuditRepository
from bot.filters.roles import can_handle_type
from bot.security.crypto import AnonCipher
from bot.services.submissions import SubmissionService, safe_send
from bot.utils.text import escape

logger = logging.getLogger(__name__)


class CardSink(Protocol):
    """A channel that displays submission cards and needs redraws."""

    async def announce(self, session: AsyncSession, submission_id: int) -> bool:
        """Post a new submission. Returns True if at least one card went out."""

    async def refresh(self, session: AsyncSession, submission_id: int) -> None:
        """Redraw the card after a status change."""


@dataclass(frozen=True)
class TakeResult:
    won: bool
    assignee_name: str  # HTML-escaped display name of whoever holds it now


def display_name(user: User | None) -> str:
    return escape(user.full_name) if user is not None and user.full_name else "—"


async def author_locale(session: AsyncSession, sub: Submission) -> str:
    """Applicant's locale for non-anonymous submissions; default ru otherwise."""
    if sub.author_user_id is not None:
        author = await session.get(User, sub.author_user_id)
        if author and author.language:
            return author.language
    return "ru"


class SubmissionActions:
    def __init__(
        self,
        session: AsyncSession,
        bot: Bot,
        core: BaseCore,
        cipher: AnonCipher,
        default_locale: str,
        sinks: tuple[CardSink, ...] | list[CardSink] = (),
    ) -> None:
        self.session = session
        self.bot = bot
        self.core = core
        self.svc = SubmissionService(session, cipher)
        self.default_locale = default_locale
        self.sinks = list(sinks)

    async def authorize(
        self, submission_id: int, user: User, *, require_owner: bool = False
    ) -> Submission | None:
        """The submission if ``user`` may act on it, else None.

        The user must be responsible for THIS submission's type (or admin).
        With ``require_owner`` (reply/close) they must also be the assignee or an
        admin — first-claim ownership, so colleagues can't act on each other's cases.
        """
        sub = await self.svc.repo.get(submission_id)
        if sub is None:
            return None
        # try_claim/close are Core UPDATEs that bypass the identity map: a
        # Submission loaded earlier in this session may hold stale status /
        # assignee. Re-read so ownership is judged on what is in the database.
        await self.session.refresh(sub)
        if not can_handle_type(user, sub.type):
            return None
        if require_owner and not user.is_admin:
            if sub.assigned_to_user_id not in (None, user.id):
                return None
        return sub

    async def take(self, sub: Submission, user: User) -> TakeResult:
        won = await self.svc.repo.try_claim(sub.id, user.id)
        if not won:
            fresh = await self.session.get(Submission, sub.id, populate_existing=True)
            assignee = (
                await self.session.get(User, fresh.assigned_to_user_id)
                if fresh is not None and fresh.assigned_to_user_id else None
            )
            return TakeResult(won=False, assignee_name=display_name(assignee))

        await self.svc.repo.record_status_event(sub.id, user.id, "new", "in_progress")
        await AuditRepository(self.session).log(
            action="status_change", actor_user_id=user.id,
            target=f"submission:{sub.id}", meta="new->in_progress",
        )
        await self.session.commit()

        name = display_name(user)
        await self.svc.update_all_cards(self.bot, self.core, sub, "card-assigned", name=name)
        await self.session.commit()
        await self._refresh(sub.id)
        return TakeResult(won=True, assignee_name=name)

    async def close(self, sub: Submission, user: User) -> bool:
        prev = await self.svc.repo.close(sub.id, user.id)
        if prev is None:
            return False
        await self.svc.repo.record_status_event(sub.id, user.id, prev, "closed")
        await AuditRepository(self.session).log(
            action="status_change", actor_user_id=user.id,
            target=f"submission:{sub.id}", meta=f"{prev}->closed",
        )
        await self.session.commit()

        await self.svc.update_all_cards(self.bot, self.core, sub, "status-closed")
        chat_id = await self.svc.resolve_author_chat_id(sub)
        if chat_id is not None:
            locale = await author_locale(self.session, sub)
            text = self.core.get("submission-closed-notify", locale, public_id=sub.public_id)
            await safe_send(self.bot, chat_id, text)
        await self.session.commit()
        await self._refresh(sub.id)
        return True

    async def reply(self, sub: Submission, user: User, text: str) -> bool:
        """Record the response and deliver it. False if the applicant is unreachable
        (blocked the bot / no chat ref) — the response row is kept either way."""
        self.session.add(
            SubmissionResponse(submission_id=sub.id, responder_user_id=user.id, text=text)
        )
        await AuditRepository(self.session).log(
            action="reply", actor_user_id=user.id, target=f"submission:{sub.id}"
        )
        await self.session.commit()

        chat_id = await self.svc.resolve_author_chat_id(sub)
        if chat_id is None:
            return False
        locale = await author_locale(self.session, sub)
        header = self.core.get("reply-to-author", locale, public_id=sub.public_id)
        body = self.core.get("reply-to-author-body", locale, text=escape(text))
        return await safe_send(self.bot, chat_id, f"{header}\n{body}")

    async def _refresh(self, submission_id: int) -> None:
        for sink in self.sinks:
            try:
                await sink.refresh(self.session, submission_id)
            except Exception:  # noqa: BLE001 — one channel must not break the action
                logger.exception("card sink refresh failed")
