"""Unit tests for the 2026 conversation-bubble extractor.

Covers:
  - _sender_matches tolerant comparison (display_name vs sender bubble)
  - _TIMESTAMP_RE catches the formats Upwork renders
  - _extract_message handles the story-container / story-header /
    story-message split + carry-forward of last_sender on bubbles
    that omit the header
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from upwork_mcp.tools.messages import (
    _TIMESTAMP_RE,
    _extract_message,
    _sender_matches,
)


# ── _sender_matches ─────────────────────────────────────────────────


@pytest.mark.parametrize("sender,me_name,expected", [
    # Exact match
    ("Vato Tchipa", "Vato Tchipa", True),
    ("james blue", "James Blue", True),  # case-insensitive
    # Display-name shorthand: "Vato T." vs "Vato Tchipa"
    ("Vato T.", "Vato Tchipa", True),
    ("Vato Tchipa", "Vato T.", True),
    # Different person
    ("James Blue", "Vato Tchipa", False),
    ("Vato Tchipa", "James Blue", False),
    # First name alone (>=3 chars)
    ("Vato", "Vato Tchipa", True),
    ("V", "Vato Tchipa", False),  # too short
    # Empty inputs
    ("", "Vato Tchipa", False),
    ("Vato Tchipa", "", False),
    (None, "Vato Tchipa", False),
])
def test_sender_matches(sender, me_name, expected):
    assert _sender_matches(sender, me_name) is expected


# ── _TIMESTAMP_RE ───────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    # Real Upwork formats observed live 2026-05-12 / 13
    ("Vato Tchipa\n   10:39 PM", "10:39 PM"),
    ("James Blue 1:10 PM PDT", "1:10 PM PDT"),
    ("9:20 PM", "9:20 PM"),
    ("12:05 AM", "12:05 AM"),
])
def test_timestamp_regex_catches_real_formats(text, expected):
    m = _TIMESTAMP_RE.search(text)
    assert m is not None
    assert m.group(0).strip() == expected


# ── _extract_message ────────────────────────────────────────────────


def _mock_bubble(*, header_text: str | None, body_text: str | None,
                me_indicator: bool = False):
    """Build a mock playwright element returning the given header/body."""
    header_el = MagicMock()
    header_el.text_content = AsyncMock(return_value=header_text)
    body_el = MagicMock()
    body_el.text_content = AsyncMock(return_value=body_text)
    me_el = MagicMock() if me_indicator else None

    bubble = MagicMock()
    async def _qs(selector):
        if "story-header" in selector:
            return header_el if header_text is not None else None
        if "story-message" in selector:
            return body_el if body_text is not None else None
        if "outgoing" in selector or "my-message" in selector:
            return me_el
        # Legacy fallback selectors
        return None
    bubble.query_selector = AsyncMock(side_effect=_qs)
    bubble.query_selector_all = AsyncMock(return_value=[])
    return bubble


@pytest.mark.asyncio
async def test_extract_message_2026_layout():
    bubble = _mock_bubble(
        header_text="Vato Tchipa\n              10:40 PM",
        body_text="lets go for 120$",
    )
    msg = await _extract_message(bubble)
    assert msg["sender"] == "Vato Tchipa"
    assert msg["timestamp"] == "10:40 PM"
    assert msg["content"] == "lets go for 120$"


@pytest.mark.asyncio
async def test_extract_message_carries_forward_sender_on_followups():
    """Upwork omits the header on consecutive bubbles from the same
    author. Carry-forward ensures the body still gets a sender."""
    bubble = _mock_bubble(header_text="", body_text="1 week deadline")
    msg = await _extract_message(
        bubble, last_sender="Vato Tchipa", last_timestamp="10:40 PM",
    )
    assert msg["sender"] == "Vato Tchipa"
    assert msg["timestamp"] == "10:40 PM"
    assert msg["content"] == "1 week deadline"


@pytest.mark.asyncio
async def test_extract_message_skips_empty_body():
    bubble = _mock_bubble(
        header_text="James Blue\n  10:39 PM",
        body_text="",
    )
    msg = await _extract_message(bubble)
    assert msg is None


# ── Sender-flip carry-forward (2026-05-17 regression) ───────────────


@pytest.mark.asyncio
async def test_extract_does_not_inherit_sender_across_speaker_flip():
    """When the previous bubble was James and the current bubble has
    NO visible header but its is_mine class hint indicates a different
    speaker, the parser MUST NOT carry James's name forward. Verified
    via CDP scroll on 2026-05-17: Vato's PropStream answer got tagged
    as 'James Blue' because the flip wasn't detected."""
    bubble = _mock_bubble(
        header_text="",       # No header rendered on the new sender's bubble
        body_text="API vs scraping — mixed. PropStream and Reonomy have APIs...",
        me_indicator=True,    # but is_mine class hint says it's the user
    )
    msg = await _extract_message(
        bubble,
        last_sender="James Blue",   # last bubble was James
        last_timestamp="2:23 AM",
        last_is_mine=False,         # last bubble was NOT mine
        me_name="Vato Tchipa",
        contact_name="James Blue",
    )
    # The new sender should NOT be James — it's the user.
    assert msg["sender"] != "James Blue"
    assert msg["sender"] == "Vato Tchipa"
    # And the timestamp should NOT carry James's 2:23 AM forward.
    assert msg.get("timestamp") != "2:23 AM"
    assert msg["is_mine"] is True


@pytest.mark.asyncio
async def test_extract_uses_contact_name_when_flip_to_contact():
    """Mirror case: previous bubble was the user, current bubble has no
    header and is_mine=False (a new contact message). Should use
    contact_name, not carry-forward the user's name."""
    bubble = _mock_bubble(
        header_text="",
        body_text="Ok, sounds good so far.",
        me_indicator=False,
    )
    msg = await _extract_message(
        bubble,
        last_sender="Vato Tchipa",
        last_timestamp="11:14 AM",
        last_is_mine=True,
        me_name="Vato Tchipa",
        contact_name="James Blue",
    )
    assert msg["sender"] == "James Blue"
    assert msg["is_mine"] is False


@pytest.mark.asyncio
async def test_extract_still_carries_when_speaker_stays_the_same():
    """Carry-forward must still work for genuine continuation bubbles
    where the speaker didn't change — this is the original use case
    and must not regress."""
    bubble = _mock_bubble(
        header_text="",
        body_text="Please answer these questions:",
        me_indicator=False,
    )
    msg = await _extract_message(
        bubble,
        last_sender="James Blue",
        last_timestamp="2:23 AM",
        last_is_mine=False,        # same speaker as before
        me_name="Vato Tchipa",
        contact_name="James Blue",
    )
    assert msg["sender"] == "James Blue"
    assert msg["timestamp"] == "2:23 AM"


@pytest.mark.asyncio
async def test_extract_explicit_header_overrides_carry_forward():
    """When the bubble HAS its own header, that wins over any
    carry-forward state — defensive against the flip logic
    accidentally clobbering a correctly-extracted sender."""
    bubble = _mock_bubble(
        header_text="Vato Tchipa\n   11:14 AM",
        body_text="API vs scraping — mixed.",
        me_indicator=True,
    )
    msg = await _extract_message(
        bubble,
        last_sender="James Blue",
        last_timestamp="2:23 AM",
        last_is_mine=False,
        me_name="Vato Tchipa",
        contact_name="James Blue",
    )
    assert msg["sender"] == "Vato Tchipa"
    assert msg["timestamp"] == "11:14 AM"
    assert msg["is_mine"] is True


@pytest.mark.asyncio
async def test_extract_flip_without_me_name_leaves_sender_unset():
    """If the caller didn't pass me_name and the flip is to the user's
    side, we can't guess the user's name — leave sender unset rather
    than carry the wrong name forward. Caller's downstream logic
    handles the resolution."""
    bubble = _mock_bubble(
        header_text="",
        body_text="some text",
        me_indicator=True,
    )
    msg = await _extract_message(
        bubble,
        last_sender="James Blue",
        last_timestamp="2:23 AM",
        last_is_mine=False,
        me_name=None,          # caller didn't pass it
        contact_name="James Blue",
    )
    # Better to have NO sender than the wrong one.
    assert msg.get("sender") != "James Blue"
    assert msg["is_mine"] is True
