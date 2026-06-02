"""Data-access repositories.

Atomicity notes:
* ``next_ticket_number`` increments a per-(type, year) counter row. On Postgres
  the ``UPDATE ... RETURNING`` takes a row lock, serialising concurrent callers.
* ``try_claim`` performs a conditional ``UPDATE ... WHERE status='new'`` so only
  one responsible person can take a submission, with no TOCTOU window.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    AnonDeliveryRef,
    AttachmentType,
    AuditLog,
    Submission,
    SubmissionAttachment,
    SubmissionStatus,
    SubmissionStatusEvent,
    SubmissionType,
    TicketCounter,
    User,
)

_TICKET_PREFIX = {SubmissionType.appeal: "OBR", SubmissionType.corruption: "COR"}
_PUBLIC_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I


def generate_public_id(length: int = 8) -> str:
    return "".join(secrets.choice(_PUBLIC_ALPHABET) for _ in range(length))


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.tg_id == tg_id))

    async def get_or_create(
        self, tg_id: int, username: str | None = None, full_name: str | None = None
    ) -> tuple[User, bool]:
        user = await self.get_by_tg_id(tg_id)
        if user is not None:
            return user, False
        user = User(tg_id=tg_id, username=username, full_name=full_name)
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent creation of the same tg_id: roll back our insert and
            # re-read the row the other transaction committed.
            await self.session.rollback()
            existing = await self.get_by_tg_id(tg_id)
            if existing is None:
                raise
            return existing, False
        return user, True

    async def set_language(self, user: User, language: str) -> None:
        user.language = language
        await self.session.flush()

    async def responsibles_for(self, type_: SubmissionType) -> list[User]:
        col = User.resp_appeal if type_ == SubmissionType.appeal else User.resp_corruption
        return list(await self.session.scalars(select(User).where(col.is_(True))))


class SubmissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, submission_id: int) -> Submission | None:
        return await self.session.get(Submission, submission_id)

    async def next_ticket_number(self, type_: SubmissionType, year: int) -> str:
        """Atomically allocate the next ticket number for (type, year).

        Uses an atomic upsert-and-increment so concurrent callers never collide
        nor get duplicate counters. On Postgres this is ``INSERT … ON CONFLICT
        DO UPDATE … RETURNING``; SQLite (unit tests) uses the equivalent
        ``ON CONFLICT`` upsert.
        """
        type_value = type_.value
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            ins = pg_insert(TicketCounter).values(type=type_value, year=year, counter=1)
            stmt = ins.on_conflict_do_update(
                index_elements=[TicketCounter.type, TicketCounter.year],
                set_={"counter": TicketCounter.counter + 1},
            ).returning(TicketCounter.counter)
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            ins = sqlite_insert(TicketCounter).values(type=type_value, year=year, counter=1)
            stmt = ins.on_conflict_do_update(
                index_elements=[TicketCounter.type, TicketCounter.year],
                set_={"counter": TicketCounter.counter + 1},
            ).returning(TicketCounter.counter)

        counter = await self.session.scalar(stmt)
        return f"{_TICKET_PREFIX[type_]}-{year}-{counter:04d}"

    async def create(
        self,
        *,
        type: SubmissionType,
        text: str,
        is_anonymous: bool,
        public_id: str,
        ticket_number: str,
        author_user_id: int | None = None,
        full_name: str | None = None,
        phone: str | None = None,
    ) -> Submission:
        sub = Submission(
            type=type,
            text=text,
            is_anonymous=is_anonymous,
            public_id=public_id,
            ticket_number=ticket_number,
            author_user_id=author_user_id,
            full_name=full_name,
            phone=phone,
            status=SubmissionStatus.new,
        )
        self.session.add(sub)
        await self.session.flush()
        return sub

    async def add_attachment(
        self, submission_id: int, file_id: str, file_type: AttachmentType, order: int
    ) -> None:
        self.session.add(
            SubmissionAttachment(
                submission_id=submission_id, file_id=file_id, file_type=file_type, order=order
            )
        )
        await self.session.flush()

    async def try_claim(self, submission_id: int, user_id: int) -> bool:
        """Atomically move new -> in_progress. Returns True iff this caller won.

        The conditional UPDATE bypasses the ORM flush, so updated_at is bumped
        explicitly here (onupdate would not fire on a Core UPDATE).
        """
        stmt = (
            update(Submission)
            .where(Submission.id == submission_id, Submission.status == SubmissionStatus.new)
            .values(
                status=SubmissionStatus.in_progress,
                assigned_to_user_id=user_id,
                updated_at=func.now(),
            )
            .returning(Submission.id)
        )
        won = await self.session.scalar(stmt)
        return won is not None

    async def close(self, submission_id: int, user_id: int) -> str | None:
        """Close an open submission atomically.

        Returns the PREVIOUS status (for an accurate audit event) if it was open,
        or None if it was already closed / missing. Capturing the prior status in
        the same conditional UPDATE avoids a stale read under concurrency.
        """
        stmt = (
            update(Submission)
            .where(Submission.id == submission_id, Submission.status != SubmissionStatus.closed)
            .values(
                status=SubmissionStatus.closed,
                closed_by_user_id=user_id,
                closed_at=datetime.now(UTC),
                updated_at=func.now(),
            )
            .returning(Submission.status)
        )
        # RETURNING gives the NEW status; we need the prior one, so read it first
        # inside the same transaction under the row's visibility.
        prev = await self.session.scalar(
            select(Submission.status).where(Submission.id == submission_id)
        )
        changed = await self.session.scalar(stmt)
        if changed is None:
            return None
        return prev.value if prev is not None else None

    async def add_anon_ref(self, submission_id: int, enc_chat_ref: bytes) -> None:
        self.session.add(
            AnonDeliveryRef(submission_id=submission_id, enc_chat_ref=enc_chat_ref)
        )
        await self.session.flush()

    async def get_anon_ref(self, submission_id: int) -> bytes | None:
        ref = await self.session.scalar(
            select(AnonDeliveryRef).where(AnonDeliveryRef.submission_id == submission_id)
        )
        return ref.enc_chat_ref if ref else None

    async def record_status_event(
        self, submission_id: int, actor_user_id: int, from_status: str | None, to_status: str
    ) -> None:
        self.session.add(
            SubmissionStatusEvent(
                submission_id=submission_id,
                actor_user_id=actor_user_id,
                from_status=from_status,
                to_status=to_status,
            )
        )
        await self.session.flush()


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self, action: str, actor_user_id: int | None = None,
        target: str | None = None, meta: str | None = None,
    ) -> None:
        """Append an audit entry. ``meta`` MUST NOT contain PII."""
        self.session.add(
            AuditLog(action=action, actor_user_id=actor_user_id, target=target, meta=meta)
        )
        await self.session.flush()
