"""Tests for the realtime notification bus (lazyclaw/notifications/realtime.py).

The WS frame shape is a published contract the mobile client builds against:
    {"type": "notification", "id", "kind", "title", "body", "created_at"}
"""

from __future__ import annotations

import asyncio
import time

import pytest

from lazyclaw.notifications import realtime

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_bus():
    realtime.clear_user("u1")
    yield
    realtime.clear_user("u1")


def _notif(**over) -> dict:
    base = {
        "id": "feed-row-1",
        "kind": "watcher_hit",
        "title": "New Upwork message",
        "body": "James replied",
        "created_at": "2026-08-09T10:00:00+00:00",
    }
    base.update(over)
    return base


async def test_frame_shape_is_exactly_the_contract():
    await realtime.emit(None, "u1", _notif())
    events = realtime.recent_events("u1")
    assert len(events) == 1
    frame = events[0].to_frame()
    assert frame == {
        "type": "notification",
        "id": "feed-row-1",
        "kind": "watcher_hit",
        "title": "New Upwork message",
        "body": "James replied",
        "created_at": "2026-08-09T10:00:00+00:00",
    }
    # No extra keys ever — the client switch-cases on this exact shape.
    assert set(frame.keys()) == {
        "type", "id", "kind", "title", "body", "created_at",
    }


async def test_publish_reaches_live_subscriber():
    received: list = []

    async def _consume():
        async for evt in realtime.subscribe("u1"):
            received.append(evt)
            return

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.01)  # let the subscriber register
    await realtime.emit(None, "u1", _notif())
    await asyncio.wait_for(task, timeout=2)
    assert len(received) == 1
    assert received[0].to_frame()["id"] == "feed-row-1"


async def test_per_user_isolation():
    await realtime.emit(None, "u1", _notif())
    assert realtime.recent_events("u2") == []
    realtime.clear_user("u2")


async def test_missing_id_gets_uuid_and_missing_created_at_gets_now():
    evt = realtime.event_from_notif("u1", {"kind": "push", "title": "t", "body": "b"})
    frame = evt.to_frame()
    assert frame["id"], "hint frames still need a non-empty id"
    assert frame["created_at"], "created_at must always be present"


async def test_recent_events_age_bound():
    stale = realtime.NotificationEvent(
        user_id="u1", id="old", kind="push", title="t", body="b",
        created_at="2026-08-09T00:00:00+00:00",
        ts=time.time() - realtime.MAX_AGE_S - 10,
    )
    realtime.publish(stale)
    await realtime.emit(None, "u1", _notif(id="fresh"))
    ids = [e.id for e in realtime.recent_events("u1")]
    assert ids == ["fresh"], "stale frames must not replay on reconnect"


async def test_emit_never_raises_on_garbage():
    await realtime.emit(None, "", _notif())        # no user
    await realtime.emit(None, "u1", None)          # not a dict
    assert realtime.recent_events("u1") == []
