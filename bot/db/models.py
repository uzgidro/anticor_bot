"""ORM models.

Enum columns use ``native_enum=False`` (stored as VARCHAR + CHECK) so Alembic
autogeneration is stable and migrations are reversible across Postgres.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


class SubmissionType(enum.StrEnum):
    appeal = "appeal"
    corruption = "corruption"


class SubmissionStatus(enum.StrEnum):
    new = "new"
    in_progress = "in_progress"
    closed = "closed"


class AttachmentType(enum.StrEnum):
    photo = "photo"
    document = "document"


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, native_enum=False, validate_strings=True, name=name, length=32)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # A user is either a Telegram user, a Matrix user, or both — never neither.
        CheckConstraint("tg_id IS NOT NULL OR matrix_id IS NOT NULL", name="ck_users_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Telegram identity. NULL for users provisioned from a Matrix room.
    tg_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    # Matrix identity (@user:server). NULL for Telegram-only users.
    matrix_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # The 1:1 room with the bot where this user's cards are delivered. NULL
    # until the user writes to the bot or the bot creates the DM on first card.
    matrix_room_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resp_appeal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resp_corruption: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Submission(TimestampMixin, Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    type: Mapped[SubmissionType] = mapped_column(_enum(SubmissionType, "submission_type"))
    status: Mapped[SubmissionStatus] = mapped_column(
        _enum(SubmissionStatus, "submission_status"),
        default=SubmissionStatus.new,
        nullable=False,
        index=True,
    )
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Only set for non-anonymous submissions (used by "My submissions").
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    assigned_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    attachments: Mapped[list[SubmissionAttachment]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionAttachment.order",
    )


class AnonDeliveryRef(Base):
    """Encrypted chat reference for anonymous submissions (key lives outside DB)."""

    __tablename__ = "anon_delivery_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enc_chat_ref: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class SubmissionAttachment(Base):
    __tablename__ = "submission_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    file_type: Mapped[AttachmentType] = mapped_column(_enum(AttachmentType, "attachment_type"))
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    submission: Mapped[Submission] = relationship(back_populates="attachments")


class SubmissionResponse(TimestampMixin, Base):
    __tablename__ = "submission_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    responder_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class SubmissionDelivery(Base):
    """A card delivered to a responsible person; lets us update buttons for all."""

    __tablename__ = "submission_deliveries"
    __table_args__ = (UniqueConstraint("submission_id", "responsible_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    responsible_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MatrixDelivery(Base):
    """An event the bot posted to a Matrix room about a submission.

    ``kind='card'`` is the editable card; ``attachment`` and ``note`` are the
    other bot events for the same submission. A reply to ANY of them resolves
    to the submission (see MatrixDeliveryRepository.submission_id_for).
    """

    __tablename__ = "matrix_deliveries"
    __table_args__ = (UniqueConstraint("room_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="card")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SubmissionStatusEvent(TimestampMixin, Base):
    __tablename__ = "submission_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)


class AuditLog(TimestampMixin, Base):
    """Immutable audit trail. ``meta`` must never contain PII."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)


class TicketCounter(Base):
    """Per (type, year) counter for atomic ticket_number generation."""

    __tablename__ = "ticket_counters"

    type: Mapped[str] = mapped_column(String(16), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
