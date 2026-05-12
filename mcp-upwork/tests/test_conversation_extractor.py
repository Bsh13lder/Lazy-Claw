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
