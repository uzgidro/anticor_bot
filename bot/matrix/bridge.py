"""Matrix bridge: submissions -> room cards, room replies -> SubmissionActions.

Every room event is handled in its own DB session (commit on success, rollback
on error) — the middleware chain does not run for Matrix events. The member
who acts is provisioned as a User on first sight with the role of the room
the message came from; from there on the rules are exactly the Telegram ones:
``SubmissionActions.authorize`` derives the type from the submission row, so
a card answered from the wrong room never grants a foreign type.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram_i18n.cores import BaseCore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot.config import MatrixSettings
from bot.db.models import AttachmentType, Submission, SubmissionType, User
from bot.db.repositories import MatrixDeliveryRepository, UserRepository
from bot.matrix.client import RoomEvent
from bot.matrix.render import parse_command, render_room_card, strip_reply_fallback
from bot.security.crypto import AnonCipher
from bot.services.actions import SubmissionActions, display_name
from bot.utils.text import escape

logger = logging.getLogger(__name__)

# Telegram Bot API refuses to download files larger than this.
MAX_DOWNLOAD = 20 * 1024 * 1024
_RECONNECT_MIN, _RECONNECT_MAX = 5, 120


class MatrixBridge:
    def __init__(
        self,
        client,
        settings: MatrixSettings,
        session_pool: async_sessionmaker,
        bot: Bot,
        core: BaseCore,
        cipher: AnonCipher,
        default_locale: str,
    ) -> None:
        self.client = client
        self.settings = settings
        self.pool = session_pool
        self.bot = bot
        self.core = core
        self.cipher = cipher
        self.default_locale = default_locale

    # --- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        """Connect and sync forever; reconnect with backoff. Never raises —
        Telegram must keep working whatever happens to Matrix."""
        self.client.on_message(self.handle_event)
        delay = _RECONNECT_MIN
        while True:
            try:
                if await self.client.connect():
                    delay = _RECONNECT_MIN
                    await self.client.run_sync()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Matrix bridge stopped; reconnecting in %ss", delay)
            finally:
                await self.client.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    # --- CardSink ----------------------------------------------------------

    async def announce(self, session: AsyncSession, submission_id: int) -> bool:
        sub = await self._load(session, submission_id)
        if sub is None:
            return False
        room = self.settings.room_for(sub.type.value)
        if not room:
            return False
        deliveries = MatrixDeliveryRepository(session)

        failed = 0
        uploaded: list[str] = []
        for index, att in enumerate(sub.attachments):
            event_id = await self._upload_attachment(room, sub.public_id, index, att)
            if event_id:
                uploaded.append(event_id)
            else:
                failed += 1

        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments), failed_attachments=failed,
        )
        card_id = await self.client.send_html(room, text)
        if not card_id:
            return False
        await deliveries.add(sub.id, room, card_id, "card")
        for event_id in uploaded:
            await deliveries.add(sub.id, room, event_id, "attachment")
        return True

    async def refresh(self, session: AsyncSession, submission_id: int) -> None:
        sub = await self._load(session, submission_id)
        if sub is None:
            return
        card = await MatrixDeliveryRepository(session).card_for(sub.id)
        if card is None:
            return
        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments),
        )
        await self.client.edit_html(card.room_id, card.event_id, text)

    # --- inbound -----------------------------------------------------------

    async def handle_event(self, event: RoomEvent) -> None:
        if event.sender == self.client.user_id or event.server_ts < self.client.started_ms:
            return
        room_type = self.settings.type_for_room(event.room_id)
        if room_type is None:
            return

        body = strip_reply_fallback(event.body)
        command, args = parse_command(body)

        if command == "yordam":
            await self.client.send_html(
                event.room_id, self.core.get("mx-help", self.settings.locale)
            )
            return

        async with self.pool() as session:
            try:
                await self._handle(session, event, room_type, body, command, args)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _handle(
        self, session: AsyncSession, event: RoomEvent, room_type: str,
        body: str, command: str | None, args: str,
    ) -> None:
        sub_id = await self._resolve(session, event, command, args)
        if sub_id is None:
            return  # ordinary conversation between staff

        name = await self.client.member_display_name(event.room_id, event.sender)
        user, _ = await UserRepository(session).get_or_create_matrix(
            event.sender, name or event.sender.lstrip("@").split(":")[0],
            SubmissionType(room_type),
        )
        actions = SubmissionActions(
            session, self.bot, self.core, self.cipher, self.default_locale, sinks=[self],
        )
        room = event.room_id

        if command == "karta":
            sub = await actions.authorize(sub_id, user)
            if sub is None:
                await self._note(room, event, "mx-forbidden")
                return
            await self._repost(session, sub)
            return

        if command == "olish":
            sub = await actions.authorize(sub_id, user)
            if sub is None:
                await self._note(room, event, "mx-forbidden")
                return
            result = await actions.take(sub, user)
            if result.won:
                await self.client.react(room, event.event_id, "👍")
            else:
                await self._note(room, event, "cb-already-taken", name=result.assignee_name)
            return

        if command == "yopish":
            sub = await actions.authorize(sub_id, user, require_owner=True)
            if sub is None:
                await self._deny_owner(session, room, event, sub_id, user)
                return
            if await actions.close(sub, user):
                await self.client.react(room, event.event_id, "👍")
            else:
                await self._note(room, event, "mx-already-closed")
            return

        if command is not None:
            await self._note(room, event, "mx-help")
            return

        if not body:
            return
        sub = await actions.authorize(sub_id, user, require_owner=True)
        if sub is None:
            await self._deny_owner(session, room, event, sub_id, user)
            return
        delivered = await actions.reply(sub, user, body)
        if delivered:
            await self.client.react(room, event.event_id, "👍")
        else:
            await self._note(room, event, "mx-reply-undeliverable")

    # --- helpers -----------------------------------------------------------

    async def _resolve(
        self, session: AsyncSession, event: RoomEvent, command: str | None, args: str
    ) -> int | None:
        if event.reply_to:
            found = await MatrixDeliveryRepository(session).submission_id_for(
                event.room_id, event.reply_to
            )
            if found is not None:
                return found
        if command in {"olish", "yopish", "karta"} and args:
            return await session.scalar(
                select(Submission.id).where(Submission.public_id == args.upper())
            )
        return None

    async def _load(self, session: AsyncSession, submission_id: int) -> Submission | None:
        # selectinload: attachments is a lazy relationship and lazy loads raise
        # under async SQLAlchemy. populate_existing: see SubmissionActions.authorize.
        return await session.scalar(
            select(Submission)
            .options(selectinload(Submission.attachments))
            .where(Submission.id == submission_id)
            .execution_options(populate_existing=True)
        )

    async def _assignee_name(self, session: AsyncSession, sub: Submission) -> str | None:
        if sub.assigned_to_user_id is None:
            return None
        return display_name(await session.get(User, sub.assigned_to_user_id))

    async def _deny_owner(self, session, room, event, sub_id, user) -> None:
        sub = await session.get(Submission, sub_id)
        if sub is not None and sub.assigned_to_user_id not in (None, user.id):
            holder = display_name(await session.get(User, sub.assigned_to_user_id))
            await self._note(room, event, "mx-not-owner", name=holder)
        else:
            await self._note(room, event, "mx-forbidden")

    async def _note(self, room: str, event: RoomEvent, key: str, **kw) -> None:
        await self.client.send_html(
            room, self.core.get(key, self.settings.locale, **kw), reply_to=event.event_id
        )

    async def _repost(self, session: AsyncSession, sub: Submission) -> None:
        sub = await self._load(session, sub.id) or sub  # attachments eagerly loaded
        room = self.settings.room_for(sub.type.value)
        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments),
        )
        event_id = await self.client.send_html(room, text)
        if event_id:
            await MatrixDeliveryRepository(session).add(sub.id, room, event_id, "card")

    async def _upload_attachment(self, room: str, public_id: str, index: int, att) -> str:
        """Pull the file from Telegram and push it into the room. '' on failure."""
        try:
            buffer = await self.bot.download(att.file_id)
            data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
        except Exception:  # noqa: BLE001 — too big, expired, network
            logger.warning("Attachment download failed for %s", public_id)
            return ""
        if not data or len(data) > MAX_DOWNLOAD:
            return ""
        if att.file_type == AttachmentType.photo:
            name, mime = f"{public_id}_{index + 1}.jpg", "image/jpeg"
        else:
            name, mime = f"{public_id}_{index + 1}", "application/octet-stream"
        return await self.client.upload_file(room, data, escape(name), mime)
