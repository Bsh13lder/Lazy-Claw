"""Smoke test: the browser event bus publishes, subscribes, and routes.

This is the zero-LLM-token observability layer that backs BrowserCanvas.
If it's broken, the live canvas shows nothing and the "$0 added to your
token bill" promise fails.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from lazyclaw.browser import event_bus


@pytest.fixture(autouse=True)
def isolate_state() -> None:
    """Each test starts with a clean per-user bus."""
    event_bus.clear_user("test-user")
    event_bus.clear_user("other-user")


def test_publish_and_recall() -> None:
    evt = event_bus.BrowserEvent(
        user_id="test-user", kind="action", action="click",
        target="#button", detail="Clicked submit",
    )
    event_bus.publish(evt)

    events = event_bus.recent_events("test-user")
    assert len(events) == 1
    assert events[0].detail == "Clicked submit"


def test_per_user_isolation() -> None:
    event_bus.publish(event_bus.BrowserEvent(
        user_id="test-user", kind="action", detail="mine",
    ))
    event_bus.publish(event_bus.BrowserEvent(
        user_id="other-user", kind="action", detail="theirs",
    ))
    mine = event_bus.recent_events("test-user")
    theirs = event_bus.recent_events("other-user")
    assert len(mine) == 1 and mine[0].detail == "mine"
    assert len(theirs) == 1 and theirs[0].detail == "theirs"


def test_thumbnail_url_stamp_and_freshness() -> None:
    """URL-stamped thumbs let the UI reject stale caches."""
    event_bus.set_thumbnail("test-user", b"fake-png", url="https://a.com")
    assert event_bus.is_thumbnail_fresh("test-user", "https://a.com") is True
    assert event_bus.is_thumbnail_fresh("test-user", "https://other.com") is False

    meta = event_bus.get_thumbnail_meta("test-user")
    assert meta is not None and meta[0] == "https://a.com"


def test_live_mode_expires() -> None:
    expiry = event_bus.set_live_mode("test-user", seconds=0.1)
    assert event_bus.is_live_mode("test-user") is True
    assert expiry > time.time()

    time.sleep(0.2)
    assert event_bus.is_live_mode("test-user") is False, (
        "live mode should auto-expire so we don't capture forever"
    )


def test_recent_events_max_age_filter() -> None:
    """Prevents a long-idle ring buffer mounting a stale canvas on reconnect.

    Regression guard for commit 47a125a.
    """
    old = event_bus.BrowserEvent(
        user_id="test-user", kind="action", detail="old",
        ts=time.time() - 600,  # 10 min ago
    )
    recent = event_bus.BrowserEvent(
        user_id="test-user", kind="action", detail="recent",
    )
    event_bus.publish(old)
    event_bus.publish(recent)
    filtered = event_bus.recent_events("test-user", limit=5, max_age_s=300)
    # Only the recent one survives the 5-min cutoff
    assert len(filtered) == 1
    assert filtered[0].detail == "recent"


def test_async_subscribe_delivers_publish() -> None:
    async def run() -> str | None:
        collected: list[event_bus.BrowserEvent] = []

        async def subscriber() -> None:
            async for evt in event_bus.subscribe("test-user"):
                collected.append(evt)
                return

        sub = asyncio.create_task(subscriber())
        await asyncio.sleep(0.01)  # let the subscriber register

        event_bus.publish(event_bus.BrowserEvent(
            user_id="test-user", kind="action", detail="live",
        ))
        try:
            await asyncio.wait_for(sub, timeout=1.0)
        except asyncio.TimeoutError:
            return None
        return collected[0].detail if collected else None

    got = asyncio.run(run())
    assert got == "live", "subscriber should receive the published event"
