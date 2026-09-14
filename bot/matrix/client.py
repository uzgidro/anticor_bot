"""Thin wrapper over matrix-nio. Knows Matrix; knows nothing about submissions.

Rooms are unencrypted by requirement, so nio is used without its e2e extra.
Events that predate ``started_ms`` are dropped by the caller (the initial sync
replays room history) and the bot's own events are never dispatched.
"""
from __future__ import annotations

import io
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nio import (
    AsyncClient,
    AsyncClientConfig,
    RoomMessageMedia,
    RoomMessageText,
    RoomPreset,
    SyncResponse,
)

from bot.config import MatrixSettings
from bot.matrix.render import html_body, plain_body

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomEvent:
    room_id: str
    sender: str
    event_id: str
    body: str
    reply_to: str
    server_ts: int


def event_reply_target(source: dict) -> str:
    relates = (source.get("content") or {}).get("m.relates_to") or {}
    return (relates.get("m.in_reply_to") or {}).get("event_id") or ""


def room_event_from_source(source: dict) -> RoomEvent:
    content = source.get("content") or {}
    return RoomEvent(
        room_id=source.get("room_id", ""),
        sender=source.get("sender", ""),
        event_id=source.get("event_id", ""),
        body=content.get("body") or "",
        reply_to=event_reply_target(source),
        server_ts=int(source.get("origin_server_ts") or 0),
    )


def _invite_members(info) -> list[str]:
    """Member ids from an invite's stripped state (m.room.member events)."""
    seen: list[str] = []
    for ev in getattr(info, "invite_state", None) or []:
        mxid = getattr(ev, "state_key", None) or getattr(ev, "sender", None)
        if mxid and mxid not in seen:
            seen.append(mxid)
    return seen


MessageHandler = Callable[[RoomEvent], Awaitable[None]]
DirectRoomHandler = Callable[[str, str], Awaitable[None]]  # (room_id, other user id)


class MatrixClient:
    def __init__(self, settings: MatrixSettings) -> None:
        self.settings = settings
        self.user_id = settings.user
        self.started_ms = 0
        self._client: AsyncClient | None = None
        self._handler: MessageHandler | None = None
        self._dm_handler: DirectRoomHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    def on_direct_room(self, handler: DirectRoomHandler) -> None:
        self._dm_handler = handler

    # --- lifecycle ---------------------------------------------------------

    async def connect(self) -> bool:
        os.makedirs(self.settings.store_dir, exist_ok=True)
        client = AsyncClient(
            self.settings.homeserver,
            self.settings.user,
            device_id=self.settings.device_name,
            store_path=self.settings.store_dir,
            config=AsyncClientConfig(request_timeout=30, max_timeout_retry_wait_time=30),
        )
        token = self.settings.token.get_secret_value()
        if token:
            client.access_token = token
            client.user_id = self.settings.user
            client.device_id = self.settings.device_name
        else:
            resp = await client.login(
                self.settings.password.get_secret_value(),
                device_name=self.settings.device_name,
            )
            if getattr(resp, "access_token", None) is None:
                logger.error("Matrix login failed: %s", type(resp).__name__)
                await client.close()
                return False
        whoami = await client.whoami()
        if not getattr(whoami, "user_id", None):
            logger.error("Matrix whoami failed: %s", type(whoami).__name__)
            await client.close()
            return False
        self.user_id = whoami.user_id
        self.started_ms = int(time.time() * 1000)
        client.add_event_callback(self._on_text, RoomMessageText)
        client.add_event_callback(self._on_text, RoomMessageMedia)
        client.add_response_callback(self._on_sync, SyncResponse)
        self._client = client
        logger.info("Matrix connected as %s", self.user_id)
        return True

    async def run_sync(self) -> None:
        assert self._client is not None
        await self._client.sync_forever(timeout=30000, full_state=False)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # --- inbound -----------------------------------------------------------

    async def _on_sync(self, response) -> None:
        """Accept pending invites. A 1:1 room is a responsible's DM — hand it to
        the bridge; anything larger just gets its id echoed for the operator."""
        assert self._client is not None
        invites = getattr(response.rooms, "invite", {}) or {}
        for room_id, info in list(invites.items()):
            result = await self._client.join(room_id)
            if not getattr(result, "room_id", None):
                logger.warning("Could not join Matrix room %s", room_id)
                continue
            logger.info("Joined Matrix room %s", room_id)
            # Members arrive with the next sync; until then the invite's
            # stripped state is the only membership we have.
            members = self._member_ids(room_id) or _invite_members(info)
            others = [m for m in members if m != self.user_id]
            if len(others) == 1 and self._dm_handler is not None:
                await self._dm_handler(room_id, others[0])
            else:
                await self.send_html(room_id, f"Room id: <code>{room_id}</code>")

    def _member_ids(self, room_id: str) -> list[str]:
        room = self._client.rooms.get(room_id) if self._client is not None else None
        return list(getattr(room, "users", {}) or {}) if room is not None else []

    async def _on_text(self, room, event) -> None:
        if self._handler is None or event.sender == self.user_id:
            return
        source = dict(event.source)
        source.setdefault("room_id", room.room_id)
        await self._handler(room_event_from_source(source))

    # --- outbound ----------------------------------------------------------

    async def send_html(
        self, room_id: str, html_text: str, *, reply_to: str | None = None
    ) -> str:
        if self._client is None or not room_id:
            return ""
        content = {
            "msgtype": "m.text",
            "body": plain_body(html_text),
            "format": "org.matrix.custom.html",
            "formatted_body": html_body(html_text),
        }
        if reply_to:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        try:
            resp = await self._client.room_send(room_id, "m.room.message", content)
        except Exception:  # noqa: BLE001
            logger.exception("Matrix send failed")
            return ""
        return getattr(resp, "event_id", "") or ""

    async def edit_html(self, room_id: str, event_id: str, html_text: str) -> bool:
        if self._client is None or not room_id or not event_id:
            return False
        new_content = {
            "msgtype": "m.text",
            "body": plain_body(html_text),
            "format": "org.matrix.custom.html",
            "formatted_body": html_body(html_text),
        }
        content = dict(new_content)
        content["body"] = "* " + content["body"]
        content["m.new_content"] = new_content
        content["m.relates_to"] = {"rel_type": "m.replace", "event_id": event_id}
        try:
            await self._client.room_send(room_id, "m.room.message", content)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Matrix edit failed")
            return False

    async def upload_file(self, room_id: str, data: bytes, name: str, mime: str) -> str:
        if self._client is None or not room_id:
            return ""
        try:
            resp, _ = await self._client.upload(
                io.BytesIO(data), content_type=mime, filename=name, filesize=len(data)
            )
            uri = getattr(resp, "content_uri", "")
            if not uri:
                logger.warning("Matrix upload rejected: %s", type(resp).__name__)
                return ""
            msgtype = "m.image" if mime.startswith("image/") else "m.file"
            sent = await self._client.room_send(
                room_id, "m.room.message",
                {"msgtype": msgtype, "body": name, "url": uri,
                 "info": {"mimetype": mime, "size": len(data)}},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Matrix upload failed")
            return ""
        return getattr(sent, "event_id", "") or ""

    async def react(self, room_id: str, event_id: str, emoji: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.room_send(
                room_id, "m.reaction",
                {"m.relates_to": {"rel_type": "m.annotation", "event_id": event_id, "key": emoji}},
            )
        except Exception:  # noqa: BLE001
            logger.debug("Matrix reaction failed", exc_info=True)

    async def create_dm(self, user_id: str) -> str:
        """Open a private room with ``user_id``. '' if the server refuses."""
        if self._client is None:
            return ""
        try:
            resp = await self._client.room_create(
                is_direct=True, invite=[user_id], preset=RoomPreset.trusted_private_chat,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Matrix room_create failed")
            return ""
        room_id = getattr(resp, "room_id", None)
        if not room_id:
            logger.warning("Matrix DM creation rejected: %s", type(resp).__name__)
            return ""
        return room_id

    async def member_display_name(self, room_id: str, user_id: str) -> str | None:
        if self._client is None:
            return None
        room = self._client.rooms.get(room_id)
        if room is None:
            return None
        member = room.users.get(user_id)
        return getattr(member, "display_name", None) or None
