"""Unit tests for the inbox-URL ``room_id`` guard (P0a) + the future-
timestamp bubble filter (P0b).

Both fixes shipped 2026-05-20 in response to a live confabulation
incident at 13:34–13:35 where the brain called
``upwork_get_conversation(room_id="https://www.upwork.com/ab/messages/rooms")``
— the bare inbox URL with no specific room appended. Because
``get_conversation_messages`` accepts URL-shaped ``room_id``s, the
browser landed on the inbox page and the bubble extractor scraped
whatever conversation happened to be open in the shared Brave profile,
producing 4 KB of garbage with a (fake-looking) "2:11 PM" timestamp.
The brain then quoted the garbage as if it were the requested thread.

See:
  feedback_quote_then_summarize.md
  feedback_most_recent_wins.md
  project_grounding_six_bug_pass.md
"""

from __future__ import annotations

import datetime as dt

import pytest

from upwork_mcp.tools.messages import (
    SendMessageParams,
    _bubble_timestamp_in_future,
    _parse_time_of_day,
    _validate_room_id,
    get_conversation_messages,
    send_message,
)


# ── P0a — inbox-URL guard, raw helper ────────────────────────────────


@pytest.mark.parametrize("bad", [
    # Bare inbox URL (the actual 2026-05-20 13:34 input)
    "https://www.upwork.com/ab/messages/rooms",
    # Trailing slash
    "https://www.upwork.com/ab/messages/rooms/",
    # Query / fragment variants
    "https://www.upwork.com/ab/messages/rooms?filter=unread",
    "https://www.upwork.com/ab/messages/rooms/?filter=unread",
    "https://www.upwork.com/ab/messages/rooms#top",
    # http:// variant (Upwork redirects but caller could feed either)
    "http://www.upwork.com/ab/messages/rooms",
    # Relative path with no id
    "/ab/messages/rooms",
    "/ab/messages/rooms/",
    "/ab/messages/rooms?filter=unread",
])
def test_validate_room_id_rejects_inbox_url(bad):
    with pytest.raises(ValueError) as excinfo:
        _validate_room_id(bad)
    assert "upwork_get_messages" in str(excinfo.value)
    assert "inbox URL" in str(excinfo.value)


@pytest.mark.parametrize("bad", [
    None,
    "",
    "   ",
    "\t\n",
])
def test_validate_room_id_rejects_empty(bad):
    with pytest.raises(ValueError) as excinfo:
        _validate_room_id(bad)
    # Same actionable error pointing the caller at the listing tool
    assert "upwork_get_messages" in str(excinfo.value)


@pytest.mark.parametrize("ok", [
    # Legacy bare id
    "room_12345abc",
    # Full conversation URL (room_<hex>)
    "https://www.upwork.com/ab/messages/rooms/room_12345abc",
    # Full URL with query string (companyReference / sidebar)
    "https://www.upwork.com/ab/messages/rooms/room_12345abc"
    "?companyReference=~01abc&sidebar=true",
    # Path-relative form that still has an id
    "/ab/messages/rooms/room_12345abc",
    # Legacy /nx/messages/ shape (still has an id segment)
    "https://www.upwork.com/nx/messages/12345",
])
def test_validate_room_id_accepts_real_ids(ok):
    # Should not raise
    _validate_room_id(ok)


# ── P0a — guard fires BEFORE any browser nav ────────────────────────


@pytest.mark.asyncio
async def test_get_conversation_messages_rejects_inbox_url_without_navigation(
    monkeypatch,
):
    """Critical: the inbox URL must be rejected BEFORE ``get_browser``
    is called. Otherwise the live Brave profile would still navigate.
    """
    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("get_browser should NOT have been called")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    with pytest.raises(ValueError) as excinfo:
        await get_conversation_messages(
            room_id="https://www.upwork.com/ab/messages/rooms"
        )
    assert "inbox URL" in str(excinfo.value)
    assert called["hit"] is False, (
        "browser must NOT be touched on inbox-URL reject"
    )


@pytest.mark.asyncio
async def test_send_message_rejects_inbox_url_without_navigation(monkeypatch):
    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("get_browser should NOT have been called")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    with pytest.raises(ValueError) as excinfo:
        await send_message(SendMessageParams(
            room_id="https://www.upwork.com/ab/messages/rooms",
            message="hi",
        ))
    assert "inbox URL" in str(excinfo.value)
    assert called["hit"] is False


# ── P0b — future-timestamp bubble filter ─────────────────────────────


def test_parse_time_of_day_handles_pm():
    assert _parse_time_of_day("2:11 PM") == (14, 11)
    assert _parse_time_of_day("10:39 PM") == (22, 39)


def test_parse_time_of_day_handles_am():
    assert _parse_time_of_day("9:20 AM") == (9, 20)
    assert _parse_time_of_day("12:00 AM") == (0, 0)
    assert _parse_time_of_day("12:00 PM") == (12, 0)


def test_parse_time_of_day_handles_timezone_suffix():
    assert _parse_time_of_day("9:20 PM PDT") == (21, 20)
    assert _parse_time_of_day("1:10 PM PDT") == (13, 10)


def test_parse_time_of_day_returns_none_on_garbage():
    assert _parse_time_of_day("") is None
    assert _parse_time_of_day(None) is None
    assert _parse_time_of_day("Last seen") is None
    assert _parse_time_of_day("99:99 PM") is None
    assert _parse_time_of_day("13:00 PM") is None  # hour > 12 with PM


def test_bubble_timestamp_in_future_keeps_past_bubbles():
    # Fixed "now" = 15:00 local. 14:11 (2:11 PM) is in the past → keep.
    fake_now = dt.datetime(2026, 5, 20, 15, 0, 0)
    assert _bubble_timestamp_in_future("2:11 PM", now=fake_now) is False
    assert _bubble_timestamp_in_future("9:20 AM", now=fake_now) is False


def test_bubble_timestamp_in_future_drops_future_bubbles():
    # Fixed "now" = 13:30. 2:11 PM = 14:11 is 41 min in the future.
    fake_now = dt.datetime(2026, 5, 20, 13, 30, 0)
    assert _bubble_timestamp_in_future("2:11 PM", now=fake_now) is True


def test_bubble_timestamp_in_future_tolerates_clock_skew():
    # Fixed "now" = 13:30:00. 13:30 (same minute) should NOT drop —
    # the parser zeros seconds, so a "13:30" timestamp evaluates to
    # delta=0 which is within the 60s skew buffer.
    fake_now = dt.datetime(2026, 5, 20, 13, 30, 30)
    assert _bubble_timestamp_in_future("13:30", now=fake_now) is False


def test_bubble_timestamp_in_future_keeps_unparseable():
    # Garbage timestamp → keep (we never want to silently drop real
    # messages on a parser failure)
    fake_now = dt.datetime(2026, 5, 20, 13, 30, 0)
    assert _bubble_timestamp_in_future("Last seen", now=fake_now) is False
    assert _bubble_timestamp_in_future("", now=fake_now) is False
    assert _bubble_timestamp_in_future(None, now=fake_now) is False
