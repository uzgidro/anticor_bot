"""Matrix bridge: submissions -> DM cards, DM replies -> SubmissionActions.

Every responsible with a Matrix id has a private room with the bot
(``users.matrix_room_id``); a new submission is delivered to every such room
of its type — the Matrix counterpart of the Telegram push card. Roles come
from the admin's /assign, never from a room. An inbound event is honoured only
if it comes from the owner of that DM, and every action then runs through
``SubmissionActions.authorize`` with the type taken from the submission row.
Every room event is handled in its own DB session (commit on success,
rollback on error) — the middleware chain does not run for Matrix events.
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
from bot.db.models import AttachmentType, Submission, User
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
        self.client.on_direct_room(self.on_direct_room)
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
        """Deliver the card (with attachments) to every responsible's DM.
        True if at least one recipient got it."""
        sub = await self._load(session, submission_id)
        if sub is None:
            return False
        users = UserRepository(session)
        deliveries = MatrixDeliveryRepository(session)
        assignee = await self._assignee_name(session, sub)
        delivered = 0
        for user in await users.matrix_responsibles_for(sub.type):
            room = await self._ensure_room(users, user)
            if not room:
                continue
            failed = 0
            uploaded: list[str] = []
            for index, att in enumerate(sub.attachments):
                event_id = await self._upload_attachment(room, sub.public_id, index, att)
                if event_id:
                    uploaded.append(event_id)
                else:
                    failed += 1
            text = render_room_card(
                self.core, self.settings.locale, sub, assignee_name=assignee,
                attachment_count=len(sub.attachments), failed_attachments=failed,
            )
            card_id = await self.client.send_html(room, text)
            if not card_id:
                continue
            await deliveries.add(sub.id, room, card_id, "card")
            for event_id in uploaded:
                await deliveries.add(sub.id, room, event_id, "attachment")
            delivered += 1
        return delivered > 0

    async def refresh(self, session: AsyncSession, submission_id: int) -> None:
        sub = await self._load(session, submission_id)
        if sub is None:
            return
        cards = await MatrixDeliveryRepository(session).cards_for(sub.id)
        if not cards:
            return
        text = render_room_card(
            self.core, self.settings.locale, sub,
            assignee_name=await self._assignee_name(session, sub),
            attachment_count=len(sub.attachments),
        )
        for card in cards:
            await self.client.edit_html(card.room_id, card.event_id, text)

    async def _ensure_room(self, users: UserRepository, user: User) -> str:
        """The user's DM room, creating and binding it on first delivery.
        '' if it cannot be created (the recipient is skipped this time)."""
        if user.matrix_room_id:
            return user.matrix_room_id
        room = await self.client.create_dm(user.matrix_id)
        if not room:
            logger.warning("Could not open a Matrix DM for a responsible; skipping")
            return ""
        await users.bind_matrix_room(user, room)
        return room

    # --- DM binding --------------------------------------------------------

    async def on_direct_room(self, room_id: str, user_id: str) -> None:
        """A person opened a 1:1 room with the bot: remember it as their DM and
        tell them their id, which is what the admin passes to /assign."""
        async with self.pool() as session:
            try:
                users = UserRepository(session)
                name = await self.client.member_display_name(room_id, user_id)
                user, _ = await users.get_or_create_by_matrix_id(user_id, name)
                await users.bind_matrix_room(user, room_id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        await self.client.send_html(
            room_id, self.core.get("mx-dm-welcome", self.settings.locale, id=escape(user_id))
        )

    # --- inbound -----------------------------------------------------------

    async def handle_event(self, event: RoomEvent) -> None:
        if event.sender == self.client.user_id or event.server_ts < self.client.started_ms:
            return
        async with self.pool() as session:
            try:
                user = await UserRepository(session).by_matrix_room(event.room_id)
                if user is None or user.matrix_id != event.sender:
                    return  # not a DM we own, or someone else inside it
                await self._handle(session, event, user)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _handle(self, session: AsyncSession, event: RoomEvent, user: User) -> None:
        body = strip_reply_fallback(event.body)
        command, args = parse_command(body)
        room = event.room_id

        if command == "yordam":
            await self.client.send_html(room, self.core.get("mx-help", self.settings.locale))
            return

        sub_id = await self._resolve(session, event, command, args)
        if sub_id is None:
            return  # ordinary conversation

        actions = SubmissionActions(
            session, self.bot, self.core, self.cipher, self.default_locale, sinks=[self],
        )

        if command == "karta":
            sub = await actions.authorize(sub_id, user)
            if sub is None:
                await self._note(room, event, "mx-forbidden")
                return
            await self._repost(session, sub, room)
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

    async def _repost(self, session: AsyncSession, sub: Submission, room: str) -> None:
        sub = await self._load(session, sub.id) or sub  # attachments eagerly loaded
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
