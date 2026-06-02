"""Submission business logic: creation (with anonymity enforcement), responsible
selection, and dispatch of cards / replies via the bot.

Anonymity enforcement is the security-critical invariant here: for anonymous
submissions we NEVER store author_user_id / full_name / phone; the author's
tg_id is kept only encrypted (AnonDeliveryRef).

Threat model (be precise): this protects against a DB dump alone — the dump has
no plaintext author for an anonymous submission. It does NOT make the author
anonymous to an operator who holds the Fernet key (ANON_ENC_KEY lives outside
the DB): key + enc_chat_ref -> tg_id, and a `users` row with that tg_id exists
in cleartext (created by UserMiddleware). There is also a residual timing-
correlation risk (users.created_at vs submissions.created_at). Mitigations for
those are tracked in the plan's follow-ups; do not overstate the guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram_i18n.cores import BaseCore
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AttachmentType, Submission, SubmissionType, User
from bot.db.repositories import (
    AuditRepository,
    SubmissionRepository,
    UserRepository,
    generate_public_id,
)
from bot.security.crypto import AnonCipher
from bot.utils.text import escape, split_text


@dataclass
class AttachmentInput:
    file_id: str
    file_type: AttachmentType


@dataclass
class SubmissionInput:
    type: SubmissionType
    text: str
    is_anonymous: bool
    author_tg_id: int
    author_user_id: int | None = None
    full_name: str | None = None
    phone: str | None = None
    attachments: list[AttachmentInput] | None = None


def _now_year() -> int:
    return datetime.now(UTC).year


class _LocaleShim:
    """Adapts a BaseCore to the ``i18n.get(key, **kw)`` interface used by keyboard
    builders, pinned to an explicit locale (for messaging other users)."""

    def __init__(self, core: BaseCore, locale: str) -> None:
        self._core = core
        self._locale = locale

    def get(self, key: str, /, *args: str, **kwargs: object) -> str:
        # Support both i18n.get("a-b") and attribute-style key "a.b" -> "a-b".
        return self._core.get(key, self._locale, **kwargs)


def _delivery(submission_id: int, responsible_user_id: int, chat_id: int, message_id: int):
    from bot.db.models import SubmissionDelivery

    return SubmissionDelivery(
        submission_id=submission_id,
        responsible_user_id=responsible_user_id,
        chat_id=chat_id,
        message_id=message_id,
    )


class SubmissionService:
    def __init__(self, session: AsyncSession, cipher: AnonCipher) -> None:
        self.session = session
        self.repo = SubmissionRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditRepository(session)
        self.cipher = cipher

    async def create(self, data: SubmissionInput) -> Submission:
        """Create a submission, enforcing anonymity. Commits via caller's session."""
        is_anon = data.is_anonymous
        # ENFORCE: anonymous submissions carry no identifying fields.
        author_user_id = None if is_anon else data.author_user_id
        full_name = None if is_anon else data.full_name
        phone = None if is_anon else data.phone

        ticket = await self.repo.next_ticket_number(data.type, year=_now_year())
        public_id = generate_public_id()
        sub = await self.repo.create(
            type=data.type,
            text=data.text,
            is_anonymous=is_anon,
            public_id=public_id,
            ticket_number=ticket,
            author_user_id=author_user_id,
            full_name=full_name,
            phone=phone,
        )
        for order, att in enumerate(data.attachments or []):
            await self.repo.add_attachment(sub.id, att.file_id, att.file_type, order)

        if is_anon:
            # Store only the encrypted, submission-bound chat ref.
            token = self.cipher.encrypt_chat_id(data.author_tg_id, submission_id=sub.id)
            await self.repo.add_anon_ref(sub.id, token)

        await self.audit.log(action="submission_created", target=f"submission:{sub.id}")
        return sub

    async def responsibles_for(self, type_: SubmissionType) -> list[User]:
        return await self.users.responsibles_for(type_)

    async def dispatch_to_responsibles(
        self, bot: Bot, core: BaseCore, sub: Submission, default_locale: str
    ) -> int:
        """Send the card to every responsible for this type, each in their locale.

        Records a SubmissionDelivery per successful send (so buttons can later be
        updated for all). Returns the count delivered. Resilient to blocked bots
        and flood control. Returns 0 if there are no responsibles.
        """
        from bot.keyboards.inline import reaction_keyboard

        responsibles = await self.users.responsibles_for(sub.type)
        delivered = 0
        for user in responsibles:
            locale = user.language or default_locale
            type_label = core.get(f"type-{sub.type.value}", locale)
            text = render_card(core, locale, sub, type_label)
            kb = reaction_keyboard(_LocaleShim(core, locale), sub.id)
            parts = split_text(text)
            # Send leading chunks without buttons; the final chunk carries them.
            ok = True
            last_message_id = None
            for i, part in enumerate(parts):
                markup = kb if i == len(parts) - 1 else None
                sent = await _send_with_retry(bot, user.tg_id, part, markup)
                if sent is None:
                    ok = False
                    break
                last_message_id = sent.message_id
            if ok and last_message_id is not None:
                self.session.add(_delivery(sub.id, user.id, user.tg_id, last_message_id))
                delivered += 1
        await self.session.flush()
        return delivered

    async def deliveries_for(self, submission_id: int):
        from sqlalchemy import select

        from bot.db.models import SubmissionDelivery

        return list(
            await self.session.scalars(
                select(SubmissionDelivery).where(
                    SubmissionDelivery.submission_id == submission_id
                )
            )
        )

    async def update_all_cards(
        self, bot: Bot, core: BaseCore, sub: Submission, status_text_key: str, **kw
    ) -> None:
        """Edit the card at every responsible's chat to reflect a status change,
        removing the action buttons. Resilient to already-deleted messages."""
        from aiogram.exceptions import TelegramBadRequest

        for d in await self.deliveries_for(sub.id):
            user = await self.session.get(User, d.responsible_user_id)
            locale = (user.language if user else None) or "ru"
            text = core.get(status_text_key, locale, **kw)
            try:
                await bot.edit_message_text(
                    text, chat_id=d.chat_id, message_id=d.message_id, reply_markup=None
                )
            except TelegramBadRequest:
                continue  # message gone / not modified

    async def resolve_author_chat_id(self, sub: Submission) -> int | None:
        """Return the chat id to deliver a reply, decrypting anon refs in memory."""
        if not sub.is_anonymous:
            if sub.author_user_id is None:
                return None
            author = await self.session.get(User, sub.author_user_id)
            return author.tg_id if author else None
        token = await self.repo.get_anon_ref(sub.id)
        if token is None:
            return None
        return self.cipher.decrypt_chat_id(token, submission_id=sub.id)


def render_card(core: BaseCore, locale: str, sub: Submission, type_label: str) -> str:
    """Render the responsible-person card in THEIR locale; body stays as written."""
    lines = [core.get("card-title", locale, public_id=sub.public_id)]
    lines.append(core.get("card-type", locale, type=type_label))
    if sub.is_anonymous:
        lines.append(core.get("card-anonymous", locale))
    else:
        if sub.full_name:
            lines.append(core.get("card-from", locale, name=escape(sub.full_name)))
        if sub.phone:
            lines.append(core.get("card-phone", locale, phone=escape(sub.phone)))
    lines.append(core.get("card-text", locale, text=escape(sub.text)))
    return "\n".join(lines)


async def _send_with_retry(bot: Bot, chat_id: int, text: str, reply_markup=None):
    """Send one message, retrying once on flood control, None if bot is blocked."""
    import asyncio

    for _attempt in range(2):
        try:
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            return None
    return None


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Send a message, swallowing 'bot blocked' and honoring flood control.

    Returns True if delivered. Used for fan-out to responsibles and replies to
    applicants so one bad recipient never breaks the rest.
    """
    import asyncio

    parts = split_text(text)
    for i, part in enumerate(parts):
        send_kwargs = kwargs if i == len(parts) - 1 else {}
        for _attempt in range(2):
            try:
                await bot.send_message(chat_id, part, **send_kwargs)
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramForbiddenError:
                return False
    return True
