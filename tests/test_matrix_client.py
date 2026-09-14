"""Pure helpers of the nio wrapper, plus the DM plumbing with nio stubbed.
Network behaviour is exercised through the bridge tests with a fake client."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import MatrixSettings
from bot.matrix.client import (
    MatrixClient,
    RoomEvent,
    event_reply_target,
    room_event_from_source,
)


def test_reply_target_extracted():
    src = {"content": {"m.relates_to": {"m.in_reply_to": {"event_id": "$card"}}}}
    assert event_reply_target(src) == "$card"
    assert event_reply_target({"content": {}}) == ""
    assert event_reply_target({}) == ""


def test_room_event_from_source():
    src = {
        "room_id": "!r:x", "sender": "@a:x", "event_id": "$e", "origin_server_ts": 1234,
        "content": {"body": "hi", "m.relates_to": {"m.in_reply_to": {"event_id": "$card"}}},
    }
    ev = room_event_from_source(src)
    assert ev == RoomEvent(
        room_id="!r:x", sender="@a:x", event_id="$e", body="hi", reply_to="$card",
        server_ts=1234,
    )


# --- DM plumbing (nio stubbed; no network) ----------------------------------

def _client_with_rooms(rooms: dict):
    """A MatrixClient whose nio client is a stub: join() succeeds and
    ``rooms`` maps room_id -> list of member ids known after the join."""
    mc = MatrixClient(MatrixSettings(homeserver="https://m.x", user="@bot:x", password="pw"))
    nio = AsyncMock()
    nio.join.side_effect = lambda rid: SimpleNamespace(room_id=rid)
    nio.room_send.return_value = SimpleNamespace(event_id="$sent")
    nio.rooms = {
        rid: SimpleNamespace(users={m: SimpleNamespace(display_name=m) for m in members})
        for rid, members in rooms.items()
    }
    mc._client = nio
    mc.user_id = "@bot:x"
    return mc, nio


def _sync_with_invites(invites: dict):
    """invites: room_id -> list of member ids carried in invite_state."""
    rooms = {}
    for rid, members in invites.items():
        state = [SimpleNamespace(sender=m, state_key=m) for m in members]
        rooms[rid] = SimpleNamespace(invite_state=state)
    return SimpleNamespace(rooms=SimpleNamespace(invite=rooms))


async def test_on_sync_routes_direct_rooms_to_dm_handler():
    mc, nio = _client_with_rooms(
        {"!dm:x": ["@bot:x", "@nodir:x"], "!grp:x": ["@bot:x", "@a:x", "@b:x"]}
    )
    seen = []

    async def dm(room_id, user_id):
        seen.append((room_id, user_id))

    mc.on_direct_room(dm)
    await mc._on_sync(_sync_with_invites({"!dm:x": [], "!grp:x": []}))

    assert seen == [("!dm:x", "@nodir:x")]
    # The group room still gets the "Room id" note, the DM does not.
    assert [c.args[0] for c in nio.room_send.await_args_list] == ["!grp:x"]


async def test_on_sync_falls_back_to_invite_state_before_members_arrive():
    mc, nio = _client_with_rooms({})  # nio knows no members yet
    seen = []

    async def dm(room_id, user_id):
        seen.append((room_id, user_id))

    mc.on_direct_room(dm)
    await mc._on_sync(_sync_with_invites({"!dm:x": ["@nodir:x", "@bot:x"]}))
    assert seen == [("!dm:x", "@nodir:x")]


async def test_create_dm_returns_room_id_or_empty():
    mc, nio = _client_with_rooms({})
    nio.room_create.return_value = SimpleNamespace(room_id="!new:x")
    assert await mc.create_dm("@nodir:x") == "!new:x"
    kwargs = nio.room_create.await_args.kwargs
    assert kwargs["is_direct"] is True and kwargs["invite"] == ["@nodir:x"]

    nio.room_create.return_value = SimpleNamespace(message="forbidden")  # RoomCreateError-like
    assert await mc.create_dm("@nodir:x") == ""
    nio.room_create.side_effect = RuntimeError("boom")
    assert await mc.create_dm("@nodir:x") == ""
