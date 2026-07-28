"""2026 rooms-list selector drift — captured live 2026-07-28.

Every `get_messages` call was logging "0 room elements matched" and
falling into the URL-mining fallback, which produced the contentless
synthetic row behind the 11-minute retry loop.

Live CDP capture against the signed-in Brave, with the rooms panel
FULLY rendered (2830 DOM elements):

    [data-test="room-item"]        ->  0 matches   <- what we searched
    [data-test="room-list-item"]   ->  1 match     <- what Upwork renders
    [data-test="rooms-panel"]      ->  1
    a[href*="/rooms/room_"]        ->  1

The widened fallback could not save it either: the row is an <a>, so
`li[class*="room"]` misses, and `div[class*="room-item"]` does not
substring-match the class `room-list-item`.

Row shape actually observed:

    <a data-test="room-list-item" href="/ab/messages/rooms/room_e09f...">
       [data-test="room-name"]              -> "James Blue, James Blue"
       [data-test="room-topic-or-subtitle"] -> "Bot Developer"
       [data-test="contact-name"]           -> (none)  # legacy hook gone

TWO defects compound here, which is why fixing only the selector would
still have returned rooms with no id:

  1. the row selector never matched;
  2. `_extract_conversation` collects candidate anchors with
     `el.query_selector_all('a[...]')` — DESCENDANTS ONLY — but the row
     IS the anchor, so every room_id pass found nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from upwork_mcp.tools.messages import _extract_conversation

LIVE_HREF = (
    "/ab/messages/rooms/room_e09f7619c1f68dd8b44544c5c4433c97"
    "?pageTitle=James"
)
LIVE_ROOM_ID = "room_e09f7619c1f68dd8b44544c5c4433c97"


def _fake_row(*, self_href=None, name_text=None, descendant_hrefs=()):
    """A Playwright-ish element handle for one rooms-list row."""
    row = MagicMock()

    async def get_attribute(attr):
        return self_href if attr == "href" else None

    row.get_attribute = AsyncMock(side_effect=get_attribute)

    async def query_selector(sel):
        if name_text is not None and "room-name" in sel:
            el = MagicMock()
            el.text_content = AsyncMock(return_value=name_text)
            return el
        return None

    row.query_selector = AsyncMock(side_effect=query_selector)

    async def query_selector_all(sel):
        out = []
        for h in descendant_hrefs:
            a = MagicMock()
            a.get_attribute = AsyncMock(return_value=h)
            out.append(a)
        return out

    row.query_selector_all = AsyncMock(side_effect=query_selector_all)
    row.text_content = AsyncMock(return_value=name_text or "")
    row.inner_text = AsyncMock(return_value=name_text or "")
    return row


class TestRowIsItselfTheAnchor:
    """The 2026 row carries the href on ITSELF, not on a child."""

    @pytest.mark.asyncio
    async def test_self_anchor_row_yields_room_id(self):
        row = _fake_row(
            self_href=LIVE_HREF,
            name_text="James Blue, James Blue",
            descendant_hrefs=(),  # exactly the live shape: no child <a>
        )
        conv = await _extract_conversation(row)
        assert conv is not None
        assert conv.get("room_id") == LIVE_ROOM_ID

    @pytest.mark.asyncio
    async def test_self_anchor_room_url_is_captured(self):
        row = _fake_row(self_href=LIVE_HREF, name_text="James Blue")
        conv = await _extract_conversation(row)
        assert LIVE_ROOM_ID in (conv or {}).get("room_url", "")


class TestDescendantAnchorStillWorks:
    """Legacy rows nested the anchor — must not regress."""

    @pytest.mark.asyncio
    async def test_descendant_anchor_row_yields_room_id(self):
        row = _fake_row(
            self_href=None,
            name_text="James Blue",
            descendant_hrefs=(LIVE_HREF,),
        )
        conv = await _extract_conversation(row)
        assert (conv or {}).get("room_id") == LIVE_ROOM_ID

    @pytest.mark.asyncio
    async def test_self_anchor_wins_over_junk_descendant(self):
        """Row href is authoritative; attachment links must not hijack it."""
        row = _fake_row(
            self_href=LIVE_HREF,
            name_text="James Blue",
            descendant_hrefs=("/ab/messages/att/deadbeef-1111-2222-3333-444455556666",),
        )
        conv = await _extract_conversation(row)
        assert (conv or {}).get("room_id") == LIVE_ROOM_ID


class TestNonRoomHrefsStillRejected:
    """The strict three-pass scan must keep rejecting non-rooms."""

    @pytest.mark.asyncio
    async def test_attachment_self_href_is_not_a_room(self):
        row = _fake_row(
            self_href="/ab/messages/att/deadbeef-1111-2222-3333-444455556666",
            name_text="James Blue",
        )
        conv = await _extract_conversation(row)
        assert not (conv or {}).get("room_id")

    @pytest.mark.asyncio
    async def test_bare_inbox_self_href_is_not_a_room(self):
        row = _fake_row(self_href="/ab/messages/rooms", name_text="James")
        conv = await _extract_conversation(row)
        assert not (conv or {}).get("room_id")


class TestRoomNameHook:
    """`room-name` is the current name hook; `contact-name` is gone."""

    @pytest.mark.asyncio
    async def test_room_name_is_used_for_contact_name(self):
        row = _fake_row(
            self_href=LIVE_HREF, name_text="James Blue, James Blue",
        )
        conv = await _extract_conversation(row)
        # Duplicated-name normalisation is existing behaviour.
        assert "James Blue" in (conv or {}).get("contact_name", "")
        assert (conv or {}).get("contact_name", "").count("James Blue") == 1
