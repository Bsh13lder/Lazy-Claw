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
from unittest.mock import AsyncMock, MagicMock

import pytest

from upwork_mcp.tools.messages import (
    MessagesParams,
    SendMessageParams,
    _bubble_timestamp_in_future,
    _extract_conversation,
    _parse_time_of_day,
    _validate_room_id,
    get_conversation_messages,
    get_messages,
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


# ── Contact-name-shaped bare id rejection (2026-05-20 21:21 incident) ─
# The brain called ``upwork_get_conversation(room_id="James Blue")`` —
# the LITERAL contact name. The inbox-URL guard didn't catch it (it
# isn't an inbox URL) and the navigator built
# ``/ab/messages/rooms/James Blue``. Upwork's SPA redirected that to
# its 404 page and the bubble extractor scraped
# ``contact_name="Looking for something?"`` as if it were real data.
#
# Defense: bare (non-URL) inputs must match one of three known shapes:
# ``room_<...>``, strict UUID, or pure numeric. Anything else (free-form
# letters, whitespace, mixed garbage) → ValueError with the same
# actionable recovery message as the inbox-URL guard.


def test_validate_room_id_rejects_contact_name_with_space():
    with pytest.raises(ValueError) as excinfo:
        _validate_room_id("James Blue")
    msg = str(excinfo.value)
    assert "contact name" in msg
    assert "upwork_get_messages" in msg


def test_validate_room_id_rejects_single_word_contact():
    # Single-word lowercase alpha doesn't match any known bare shape
    # (not ``room_<...>``, not UUID 8-4-4-4-12, not pure numeric).
    with pytest.raises(ValueError) as excinfo:
        _validate_room_id("James")
    msg = str(excinfo.value)
    assert "contact name" in msg
    assert "upwork_get_messages" in msg


@pytest.mark.parametrize("bad", [
    "James Blue",
    "James",
    "this is not an id",
    "123 abc",
    "room_abc/extra",  # bare id with slash garbage
])
def test_validate_room_id_rejects_various_contact_shapes(bad):
    with pytest.raises(ValueError) as excinfo:
        _validate_room_id(bad)
    assert "upwork_get_messages" in str(excinfo.value)


def test_validate_room_id_accepts_room_hex_bare_id():
    # Preserve existing behavior — legacy ``room_<hex>`` bare ids
    # must continue to pass.
    _validate_room_id("room_abc123")


def test_validate_room_id_accepts_bare_uuid():
    # 2026 migration shape — strict 8-4-4-4-12 lowercase hex is a
    # legitimate bare room id.
    _validate_room_id("9992d280-e432-470c-9c31-798aa07fcb1e")


def test_validate_room_id_accepts_numeric_bare_id():
    # Legacy numeric ids (the /nx/messages/<digits> shape) — still
    # pass as bare strings.
    _validate_room_id("12345")


@pytest.mark.parametrize("ok", [
    "https://www.upwork.com/ab/messages/rooms/room_abc123",
    "https://www.upwork.com/ab/messages/rooms/"
    "9992d280-e432-470c-9c31-798aa07fcb1e",
    "https://www.upwork.com/nx/messages/12345",
    "/ab/messages/rooms/room_abc123",
    "/ab/messages/rooms/9992d280-e432-470c-9c31-798aa07fcb1e",
    "/nx/messages/12345",
])
def test_validate_room_id_accepts_full_urls(ok):
    # All URL-shaped (http(s):// or /) variants with a real id keep
    # working — the new strict-bare check does NOT apply to URLs.
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


def test_bubble_timestamp_in_future_keeps_time_only_strings():
    # Date-aware semantics (2026-05-21 fix): plain time-of-day with no
    # date prefix is AMBIGUOUS (could be yesterday's bubble, not future)
    # → KEEP. Pre-fix this dropped as "future" and silently swallowed
    # real recent contact messages (the 2026-05-21 22:13 James Blue
    # incident: 4 yesterday-bubbles dropped because 22:37 > 22:14 now).
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("2:11 PM", now=fake_now) is False
    assert _bubble_timestamp_in_future("10:37 PM", now=fake_now) is False
    assert _bubble_timestamp_in_future("11:59 PM", now=fake_now) is False


def test_bubble_timestamp_in_future_tolerates_clock_skew():
    # Fixed "now" = 13:30:00. 13:30 (same minute, same day) should NOT
    # drop — bubble is time-only with no date hint, so post-fix it's
    # auto-KEEP regardless of clock skew.
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 30)
    assert _bubble_timestamp_in_future("13:30", now=fake_now) is False


def test_bubble_timestamp_in_future_keeps_unparseable():
    # Garbage timestamp → keep (we never want to silently drop real
    # messages on a parser failure)
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("Last seen", now=fake_now) is False
    assert _bubble_timestamp_in_future("", now=fake_now) is False
    assert _bubble_timestamp_in_future(None, now=fake_now) is False


# ── Date-aware semantics: explicit date hints DO let us drop futures ──


def test_bubble_drops_tomorrow_prefix():
    # "Tomorrow" is definitionally future — drop regardless of time.
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("Tomorrow 9:00 AM", now=fake_now) is True
    assert _bubble_timestamp_in_future("tomorrow 11:59 PM", now=fake_now) is True


def test_bubble_keeps_yesterday_prefix():
    # "Yesterday" is definitionally past — keep regardless of time.
    fake_now = dt.datetime(2026, 5, 21, 8, 0, 0)
    assert _bubble_timestamp_in_future("Yesterday 10:37 PM", now=fake_now) is False
    assert _bubble_timestamp_in_future("yesterday 11:59 PM", now=fake_now) is False


def test_bubble_drops_today_future_time():
    # "Today 11:00 PM" with now = 10:00 PM → drop.
    fake_now = dt.datetime(2026, 5, 21, 22, 0, 0)
    assert _bubble_timestamp_in_future("Today 11:00 PM", now=fake_now) is True


def test_bubble_keeps_today_past_time():
    # "Today 10:00 AM" with now = 10:00 PM → keep.
    fake_now = dt.datetime(2026, 5, 21, 22, 0, 0)
    assert _bubble_timestamp_in_future("Today 10:00 AM", now=fake_now) is False


def test_bubble_drops_iso_future_date():
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("2026-05-22 10:00 AM", now=fake_now) is True


def test_bubble_keeps_iso_past_date():
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("2026-05-20 10:00 AM", now=fake_now) is False


def test_bubble_drops_slash_future_date():
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("5/22 10:00 AM", now=fake_now) is True


def test_bubble_keeps_slash_past_date():
    fake_now = dt.datetime(2026, 5, 21, 13, 30, 0)
    assert _bubble_timestamp_in_future("5/20 10:00 AM", now=fake_now) is False


# ── P0c — _extract_conversation must NEVER seed downstream with the
# inbox URL as room_url. This is the WRITE-side of the same defense
# tested above on the READ side (_validate_room_id). The 2026-05-20
# James Blue incident (six failed get_conversation attempts) traced
# back to the extractor stamping the inbox URL into ``room_url`` when
# the first /messages/ anchor on the row was a nav/skeleton link.


def _mock_anchor(href: str | None):
    a = MagicMock()
    a.get_attribute = AsyncMock(return_value=href)
    return a


def _mock_row(*, contact_name: str | None, anchors: list[MagicMock]):
    """Build a mock .desktop-layout-room element.

    contact_name=None forces the row-text fallback to find nothing,
    making _extract_conversation return None (used to assert behavior
    on contact-less rows). For these tests we always pass a name so
    the function reaches the room-URL block.
    """
    name_el = None
    if contact_name is not None:
        name_el = MagicMock()
        name_el.text_content = AsyncMock(return_value=contact_name)

    row = MagicMock()

    async def _qs(selector):
        # contact-name selector group
        if "contact-name" in selector or "user-name" in selector or "sender-name" in selector:
            return name_el
        # preview / timestamp / unread / related-job — return None
        return None

    async def _qsa(selector):
        if 'a[href*="/messages/"]' in selector:
            return list(anchors)
        return []

    row.query_selector = AsyncMock(side_effect=_qs)
    row.query_selector_all = AsyncMock(side_effect=_qsa)
    row.text_content = AsyncMock(return_value=contact_name or "")
    return row


@pytest.mark.asyncio
async def test_extract_conversation_skips_inbox_url_anchor():
    """Row whose ONLY message anchor is the inbox URL must yield NO
    room_url / room_id. Partial conv is safer than seeding downstream
    with the inbox URL — that is what produced the 2026-05-20 James
    Blue confabulation loop (brain called get_conversation six times
    with the inbox URL because get_messages put it in room_url).
    """
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor("https://www.upwork.com/ab/messages/rooms")],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["contact_name"] == "James Blue"
    assert "room_url" not in conv, (
        "inbox URL must NEVER be written into room_url — downstream "
        "get_conversation will reject it"
    )
    assert "room_id" not in conv


@pytest.mark.asyncio
async def test_extract_conversation_skips_inbox_url_variants():
    """All inbox-URL shapes (trailing slash, query, fragment, http,
    relative) must be skipped — matching the inputs the WRITE-side
    defense must cover that the READ-side validator already rejects.
    """
    bad_inbox_hrefs = [
        "https://www.upwork.com/ab/messages/rooms",
        "https://www.upwork.com/ab/messages/rooms/",
        "https://www.upwork.com/ab/messages/rooms?filter=unread",
        "http://www.upwork.com/ab/messages/rooms",
        "/ab/messages/rooms",
        "/ab/messages/rooms/",
    ]
    for bad in bad_inbox_hrefs:
        row = _mock_row(
            contact_name="James Blue",
            anchors=[_mock_anchor(bad)],
        )
        conv = await _extract_conversation(row)
        assert conv is not None
        assert "room_url" not in conv, (
            f"inbox URL leaked into room_url for href={bad!r}"
        )
        assert "room_id" not in conv


@pytest.mark.asyncio
async def test_extract_conversation_prefers_room_hex_over_inbox_anchor():
    """When a row contains BOTH a skeleton inbox anchor (first in DOM
    order — the exact failure shape) AND a real room_<hex> anchor,
    the real one must win regardless of order.
    """
    row = _mock_row(
        contact_name="James Blue",
        anchors=[
            _mock_anchor("https://www.upwork.com/ab/messages/rooms"),
            _mock_anchor(
                "https://www.upwork.com/ab/messages/rooms/"
                "room_abc123?companyReference=~01x&sidebar=true"
            ),
        ],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["room_id"] == "room_abc123"
    assert conv["room_url"].startswith(
        "https://www.upwork.com/ab/messages/rooms/room_abc123"
    )


@pytest.mark.asyncio
async def test_extract_conversation_accepts_legacy_nx_messages_id():
    """Legacy /nx/messages/<id> shape should still work."""
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor("/nx/messages/12345")],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["room_id"] == "12345"
    assert conv["room_url"] == "https://www.upwork.com/nx/messages/12345"


@pytest.mark.asyncio
async def test_extract_conversation_relative_room_hex_href():
    """Path-relative room_<hex> anchor must be absolutized."""
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor("/ab/messages/rooms/room_def456")],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["room_id"] == "room_def456"
    assert conv["room_url"] == (
        "https://www.upwork.com/ab/messages/rooms/room_def456"
    )


# ── P0c (UUID shape) — strict UUID at /ab/messages/rooms/<uuid> only
# Earlier today (2026-05-20 ~16:40) the extractor was hardened to
# reject ALL UUID anchors after a James Blue confabulation loop where
# non-room UUIDs were extracted from the inbox row DOM. That fix was
# too strict: at 19:23:55 the WARNING fired —
#     "room-list row had 5 message anchors but none resolved to a
#      room_<hex> or legacy /nx/messages/<numeric-id>"
# Investigation showed Upwork has migrated some/all real rooms to
# UUID-shape paths (``/ab/messages/rooms/<uuid>``). James Blue's row
# in the current DOM has NO ``room_<hex>`` anchor at all — so the
# row became unreachable.
#
# Restored fix (now pinned by these tests):
#   • Pass 1: room_<hex> always wins, break.
#   • Pass 2: strict UUID at /ab/messages/rooms/<uuid> only
#       (path-equality, lowercase hex, no intermediate or trailing
#       segments). Accept first match, break.
#   • Pass 3: legacy /nx/messages/<id> only when <id> is numeric.
#   • /att/, /file/ subpaths still rejected (intermediate segment).
#   • Uppercase UUIDs still rejected (Upwork uses lowercase hex).
#   • UUID with trailing segment still rejected
#       (e.g. /ab/messages/rooms/<uuid>/something).
#   • Bare inbox URL still rejected.


@pytest.mark.asyncio
async def test_extract_conversation_accepts_strict_uuid_only_row():
    """``/ab/messages/rooms/<uuid>`` is the 2026 migration shape for a
    real room. When the ONLY message anchor on a row is a strict UUID
    at that exact path, the extractor MUST accept it — otherwise rows
    that have migrated away from ``room_<hex>`` become unreachable
    (live incident: James Blue 2026-05-20 19:23:55 warning).
    """
    uuid_str = "9992d280-e432-470c-9c31-798aa07fcb1e"
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(f"/ab/messages/rooms/{uuid_str}")],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["contact_name"] == "James Blue"
    assert conv["room_id"] == uuid_str, (
        "strict UUID at /ab/messages/rooms/<uuid> must be accepted — "
        "Upwork has migrated some rooms to UUID-shape paths"
    )
    assert conv["room_url"] == (
        f"https://www.upwork.com/ab/messages/rooms/{uuid_str}"
    )


@pytest.mark.asyncio
async def test_extract_conversation_accepts_uuid_with_query_and_fragment():
    """Strict UUID room URLs often carry ``?companyReference=...`` and
    similar query strings — those must be stripped before path-equality
    matching so the UUID is still accepted.
    """
    uuid_str = "9992d280-e432-470c-9c31-798aa07fcb1e"
    href = (
        f"/ab/messages/rooms/{uuid_str}"
        "?companyReference=~01x&sidebar=true"
    )
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(href)],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["room_id"] == uuid_str
    # room_url preserves the full original href (query + fragment).
    assert conv["room_url"].startswith(
        f"https://www.upwork.com/ab/messages/rooms/{uuid_str}"
    )


@pytest.mark.asyncio
async def test_extract_conversation_rejects_uppercase_uuid():
    """Upwork serves real room ids as lowercase hex UUIDs. An anchor
    using uppercase hex is NOT a real room URL shape — reject it to
    avoid seeding downstream with an id Upwork won't recognize.
    """
    upper_uuid = "9992D280-E432-470C-9C31-798AA07FCB1E"
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(f"/ab/messages/rooms/{upper_uuid}")],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert "room_id" not in conv, (
        "uppercase-hex UUIDs are not Upwork's real room-id shape"
    )
    assert "room_url" not in conv


@pytest.mark.asyncio
async def test_extract_conversation_rejects_uuid_with_trailing_segment():
    """The strict UUID match requires the path be EXACTLY
    ``/ab/messages/rooms/<uuid>`` (with optional trailing slash).
    A trailing path segment (``/something`` after the UUID) means the
    anchor points at a sub-resource (attachment preview, sidebar pane,
    etc.) — NOT the room itself. Must be rejected.
    """
    uuid_str = "9992d280-e432-470c-9c31-798aa07fcb1e"
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(
            f"/ab/messages/rooms/{uuid_str}/something"
        )],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert "room_id" not in conv, (
        "UUID with trailing path segment is a sub-resource, not a room"
    )
    assert "room_url" not in conv


@pytest.mark.asyncio
async def test_extract_conversation_rejects_attachment_uuid_anchor():
    """``/ab/messages/att/<uuid>`` is an attachment anchor — must be
    fully ignored. The old "any non-rooms first segment" branch was
    harvesting ``att`` as a room_id (debris visible in
    ``lazyclaw_seen_rooms.json``). Now strictly rejected.
    """
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(
            "/ab/messages/att/9992d280-e432-470c-9c31-798aa07fcb1e"
        )],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert "room_id" not in conv, (
        "attachment anchor (/att/<uuid>) must never produce a room_id"
    )
    assert "room_url" not in conv


@pytest.mark.asyncio
async def test_extract_conversation_rejects_file_subpath():
    """``/ab/messages/file/...`` is a file-preview anchor — must be
    fully ignored. Same defense as the /att/ rejection above.
    """
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(
            "/ab/messages/file/9992d280-e432-470c-9c31-798aa07fcb1e"
        )],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert "room_id" not in conv, (
        "file-preview anchor (/file/...) must never produce a room_id"
    )
    assert "room_url" not in conv


@pytest.mark.asyncio
async def test_extract_conversation_legacy_nx_requires_numeric_id():
    """Legacy ``/nx/messages/<id>`` is only accepted when ``<id>`` is
    numeric. Upwork's legacy ids are always numeric — alphanumeric
    slugs under /nx/messages/ are routes (settings, search, etc.),
    not real conversation ids. ``/nx/messages/abc`` must be rejected
    and ``/nx/messages/12345`` must be accepted.
    """
    # Rejected: alphanumeric slug
    bad = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor("/nx/messages/abc")],
    )
    bad_conv = await _extract_conversation(bad)
    assert bad_conv is not None
    assert "room_id" not in bad_conv, (
        "alphanumeric /nx/messages/<slug> is a route, not a room id"
    )
    assert "room_url" not in bad_conv

    # Accepted: numeric id
    good = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor("/nx/messages/12345")],
    )
    good_conv = await _extract_conversation(good)
    assert good_conv is not None
    assert good_conv["room_id"] == "12345"
    assert good_conv["room_url"] == (
        "https://www.upwork.com/nx/messages/12345"
    )


@pytest.mark.asyncio
async def test_extract_conversation_warning_includes_scanned_hrefs(caplog):
    """When no anchor matches a known room-id shape, the diagnostic
    warning MUST include the actual hrefs we scanned. Without this,
    post-mortem analysis can't tell whether the row genuinely lacks a
    room link or Upwork shipped a layout we don't recognize yet.

    The 2026-05-20 21:21 incident motivated this: the WARNING was
    firing on James Blue's row, but we couldn't tell whether Upwork
    was serving attachment-only anchors, a brand-new URL shape, or
    something else entirely. The diagnostic must dump the raw href
    strings so the next layout-drift incident is debuggable from logs
    alone.
    """
    import logging as _logging

    href_a = "/ab/messages/att/9992d280-e432-470c-9c31-798aa07fcb1e"
    href_b = (
        "/ab/messages/file/"
        "abcdef01-2345-6789-abcd-ef0123456789"
    )
    row = _mock_row(
        contact_name="James Blue",
        anchors=[_mock_anchor(href_a), _mock_anchor(href_b)],
    )

    with caplog.at_level(_logging.WARNING, logger="upwork_mcp.tools.messages"):
        conv = await _extract_conversation(row)

    # No room_id was resolved — both anchors are attachment subpaths.
    assert conv is not None
    assert "room_id" not in conv
    assert "room_url" not in conv

    # The warning must mention BOTH hrefs verbatim so a human can
    # diagnose the layout drift from logs alone.
    warning_text = "\n".join(
        r.getMessage() for r in caplog.records if r.levelno == _logging.WARNING
    )
    assert href_a in warning_text, (
        f"diagnostic warning missing href_a={href_a!r}; "
        f"got: {warning_text!r}"
    )
    assert href_b in warning_text, (
        f"diagnostic warning missing href_b={href_b!r}; "
        f"got: {warning_text!r}"
    )


@pytest.mark.asyncio
async def test_extract_conversation_prefers_room_hex_over_uuid():
    """If a row contains BOTH a UUID anchor first AND a ``room_<hex>``
    anchor second, the ``room_<hex>`` wins because it's the only
    legitimate room URL shape. The loop must lock on the room_<hex>
    and ``break``, NOT ``continue`` (the prior bug let the last UUID
    overwrite the real room_<hex> on its second pass).
    """
    row = _mock_row(
        contact_name="James Blue",
        anchors=[
            _mock_anchor(
                "/ab/messages/rooms/"
                "9992d280-e432-470c-9c31-798aa07fcb1e"
            ),
            _mock_anchor(
                "https://www.upwork.com/ab/messages/rooms/"
                "room_abc123?companyReference=~01x&sidebar=true"
            ),
        ],
    )
    conv = await _extract_conversation(row)
    assert conv is not None
    assert conv["room_id"] == "room_abc123"
    assert conv["room_url"].startswith(
        "https://www.upwork.com/ab/messages/rooms/room_abc123"
    )


# ── P0d — URL-fallback when sidebar empty on conversation view ───────
# Live incident 2026-05-21 12:58:33: the host Brave tab was on James
# Blue's open conversation view at
# /ab/messages/rooms/room_e09f7619c1f68dd8b44544c5c4433c97. `safe_goto`
# navigated to /ab/messages/rooms/ but Upwork's SPA re-opened the last
# conversation — so the rooms-list sidebar never rendered, both
# [data-test="rooms-panel"] and [data-test="room-item"] returned 0,
# and `get_messages` returned `[]`. The real `room_<hex>` was sitting
# in `page.url` the whole time.
#
# Fix: when row extraction yields no conversations AND the page URL
# matches a real room-id shape, synthesize a single conversation from
# the URL (+ ``page.title()`` for the contact name). The pre-existing
# "0 row elements matched" WARNING still fires for the genuine empty-
# inbox / layout-drift case — only the conversation-view shape gets
# recovered.


@pytest.mark.asyncio
async def test_get_messages_url_fallback_returns_open_conversation(monkeypatch):
    """Sidebar empty + conversation view open → synthesize one conv from page.url."""

    real_room_id = "room_e09f7619c1f68dd8b44544c5c4433c97"
    cur_url = (
        f"https://www.upwork.com/ab/messages/rooms/{real_room_id}"
        "?companyReference=1604796102421520385&sidebar=true"
    )

    fake_page = MagicMock()
    fake_page.url = cur_url
    # Conversation-view title is typically the contact's name.
    fake_page.title = AsyncMock(return_value="James Blue")
    # Stability poll + extraction both call query_selector_all — all
    # selectors return empty (sidebar didn't render).
    fake_page.query_selector_all = AsyncMock(return_value=[])
    fake_page.query_selector = AsyncMock(return_value=None)
    fake_page.wait_for_selector = AsyncMock(return_value=None)
    fake_page.wait_for_load_state = AsyncMock(return_value=None)

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=None)
    fake_browser.safe_goto = AsyncMock(return_value=fake_page)

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )
    # Isolate seen-rooms persistence so the test doesn't write to the
    # user's real profile dir.
    monkeypatch.setattr(
        "upwork_mcp.tools.messages._load_seen_rooms", lambda: set()
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages._save_seen_rooms", lambda _: None
    )

    result = await get_messages(MessagesParams())

    assert len(result) == 1, (
        f"expected 1 synthesized conversation from page.url, got {result!r}"
    )
    conv = result[0]
    assert conv["room_id"] == real_room_id
    assert conv["contact_name"] == "James Blue"
    assert conv["source"] == "page_url_fallback"
    # room_url preserves the full URL (including query string) so
    # downstream `safe_goto` can re-navigate without losing context.
    assert conv["room_url"] == cur_url


@pytest.mark.asyncio
async def test_get_messages_url_fallback_strips_generic_titles(monkeypatch):
    """Generic Upwork inbox titles (Messages / Upwork) must NOT be
    treated as a contact name — they're the default page title when no
    real conversation is open. Fall back to empty contact_name so the
    brain doesn't quote "Messages" as a person's name.
    """
    real_room_id = "9992d280-e432-470c-9c31-798aa07fcb1e"
    cur_url = f"https://www.upwork.com/ab/messages/rooms/{real_room_id}"

    fake_page = MagicMock()
    fake_page.url = cur_url
    fake_page.title = AsyncMock(return_value="Messages | Upwork")
    fake_page.query_selector_all = AsyncMock(return_value=[])
    fake_page.query_selector = AsyncMock(return_value=None)
    fake_page.wait_for_selector = AsyncMock(return_value=None)
    fake_page.wait_for_load_state = AsyncMock(return_value=None)

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=None)
    fake_browser.safe_goto = AsyncMock(return_value=fake_page)

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages._load_seen_rooms", lambda: set()
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages._save_seen_rooms", lambda _: None
    )

    result = await get_messages(MessagesParams())

    assert len(result) == 1
    conv = result[0]
    assert conv["room_id"] == real_room_id
    assert conv["contact_name"] == "", (
        "generic 'Messages | Upwork' title must NOT become a contact_name"
    )
    assert conv["source"] == "page_url_fallback"


@pytest.mark.asyncio
async def test_get_messages_url_fallback_does_not_fire_on_inbox_url(monkeypatch):
    """When ``page.url`` is the bare inbox URL (no room id segment), the
    fallback must NOT synthesize anything — that's the genuine empty-
    inbox / layout-drift case and the pre-existing "0 rows" warning is
    the correct signal.
    """
    fake_page = MagicMock()
    # Bare inbox URL — exactly what ``safe_goto`` navigated to.
    fake_page.url = "https://www.upwork.com/ab/messages/rooms/"
    fake_page.title = AsyncMock(return_value="James Blue")
    fake_page.query_selector_all = AsyncMock(return_value=[])
    fake_page.query_selector = AsyncMock(return_value=None)
    fake_page.wait_for_selector = AsyncMock(return_value=None)
    fake_page.wait_for_load_state = AsyncMock(return_value=None)

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=None)
    fake_browser.safe_goto = AsyncMock(return_value=fake_page)

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages._load_seen_rooms", lambda: set()
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages._save_seen_rooms", lambda _: None
    )

    result = await get_messages(MessagesParams())

    assert result == [], (
        "no fallback synthesis when URL has no room-id segment "
        f"(got {result!r})"
    )


# ── Fix 3 — a11y-tree fallback for get_conversation_messages ─────────
# Live diagnosis 2026-05-21: James Blue's room returned silently empty
# from get_conversation_messages — no `_extract_message`-level warnings,
# no future-timestamp filter drops. Strong signal that
# `query_selector_all('[data-test="story-container"]')` returned 0
# (selector drift, same shape as the inbox-sidebar bug we fixed earlier).
# Fix 3 adds an a11y-tree fallback that flattens
# `page.accessibility.snapshot()` into a readable text dump so the brain
# can quote from it even when DOM hooks have drifted. The regular
# extraction loop is still preferred when any container matched — the
# a11y path only fires when both the primary AND widened selectors
# return empty.


@pytest.mark.asyncio
async def test_get_conversation_messages_a11y_fallback_when_bubbles_empty(
    monkeypatch,
):
    """When ALL bubble selectors return empty AND ``page.url`` matches a
    real room shape, the function emits a flattened a11y tree as
    ``dom_fallback`` instead of returning a useless empty messages list.
    """
    real_room_id = "room_e09f7619c1f68dd8b44544c5c4433c97"
    cur_url = (
        f"https://www.upwork.com/ab/messages/rooms/{real_room_id}"
        "?companyReference=1604796102421520385&sidebar=true"
    )

    fake_page = MagicMock()
    fake_page.url = cur_url
    # Every DOM selector returns empty → forces the a11y branch.
    fake_page.query_selector_all = AsyncMock(return_value=[])
    fake_page.query_selector = AsyncMock(return_value=None)
    fake_page.title = AsyncMock(return_value="James Blue")
    fake_page.wait_for_load_state = AsyncMock(return_value=None)
    # Stability-poll JS evaluator returns the no-scroller / zero-count tick.
    fake_page.evaluate = AsyncMock(return_value={"scrolled": False, "count": 0})

    # Tiny ARIA tree — chat pane with one outgoing message.
    fake_tree = {
        "role": "region",
        "name": "Chat",
        "children": [
            {
                "role": "text",
                "name": "Hello James",
                "children": [],
            },
            {
                "role": "button",
                "name": "Send",
                "children": [],
            },
        ],
    }
    fake_accessibility = MagicMock()
    fake_accessibility.snapshot = AsyncMock(return_value=fake_tree)
    fake_page.accessibility = fake_accessibility

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=None)
    fake_browser.safe_goto = AsyncMock(return_value=fake_page)

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )

    result = await get_conversation_messages(real_room_id)

    assert result["source"] == "a11y_fallback", (
        f"expected a11y_fallback source, got {result!r}"
    )
    assert "dom_fallback" in result
    assert "Hello James" in result["dom_fallback"], (
        "a11y dump must include the chat message text so the brain "
        "can quote from it"
    )
    assert result["room_id"] == real_room_id
    assert result["messages"] == []
    assert result["room_url"] == cur_url
