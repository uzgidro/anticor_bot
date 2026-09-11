"""Pure helpers of the nio wrapper. Network behaviour is exercised through the
bridge tests with a fake client."""
from bot.matrix.client import RoomEvent, event_reply_target, room_event_from_source


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
