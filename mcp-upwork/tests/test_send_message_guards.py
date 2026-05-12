"""Unit tests for the URL guard + product-pitch guard on send_message.

Both guards are HARD blockers — they short-circuit BEFORE any browser
navigation happens, so a bad message never reaches Upwork's filter (no
wasted send, no flagged account). They also return a structured
{"status": "blocked", "offending_token": ...} so the caller (brain or
NL skill) can echo the token and ask the user to rephrase.

Past incidents both happened on 2026-05-12 — see
  feedback_upwork_dm_no_links.md
  feedback_upwork_no_lazyclaw_product_pitch.md
"""

from __future__ import annotations

import pytest

from upwork_mcp.tools.messages import (
    SendMessageParams,
    _contains_product_pitch,
    _contains_url,
    send_message,
)


# ── URL guard ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected_kind", [
    # Real incident: github URL in dictated reply
    ("check my portfolio at github.com/Bsh13lder/Lazy-Claw", "github.com"),
    ("https://example.com/portfolio", "https://"),
    ("see http://my-site.dev for samples", "http://"),
    ("www.linkedin.com/in/vato", "www.linkedin.com"),
    # Bare domain.tld
    ("happy to share — check bit.ly/abc", "bit.ly"),
    ("my work is on upwork.com obviously", "upwork.com"),  # even Upwork itself
])
def test_contains_url_catches_real_patterns(text, expected_kind):
    hit = _contains_url(text)
    assert hit is not None, f"URL guard missed: {text!r}"
    assert expected_kind.lower() in hit.lower(), (
        f"Expected hit containing {expected_kind!r}, got {hit!r}"
    )


@pytest.mark.parametrize("text", [
    "check my portfolio",
    "happy to share work samples on request",
    "this is familiar territory for me",
    "the 24/7 monitoring, weekly sweeps, city filters",
    "I can ship this in 4 days",
    "$25/hr",
    "version 3.14",
    "10.5 stars",  # not a TLD
    "say 'hello world'",
    "",
])
def test_contains_url_passes_clean_text(text):
    assert _contains_url(text) is None, (
        f"URL guard FALSE-POSITIVE on clean text: {text!r}"
    )


# ── Product-pitch guard ─────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "LazyClaw runs an undetectable browser via CDP",
    "I built LazyClaw",
    "our lazyclaw stack",
    "Lazy Claw drives your browser",
    "lazy claw is fast",
    "use LazyClaw for the scrape",
])
def test_contains_product_pitch_catches_lazyclaw_mentions(text):
    assert _contains_product_pitch(text) is not None, (
        f"Product-pitch guard missed: {text!r}"
    )


@pytest.mark.parametrize("text", [
    "I drive your existing Brave/Chrome over CDP with your real cookies",
    "Python + Scrapy + BeautifulSoup automation",
    "daily logs, human-in-loop review",
    "claws are sharp",  # word "claws" should NOT match "lazy claw"
    "lazy people don't ship",  # word "lazy" alone should NOT match
    "",
])
def test_contains_product_pitch_passes_clean_text(text):
    assert _contains_product_pitch(text) is None, (
        f"Product-pitch guard FALSE-POSITIVE: {text!r}"
    )


# ── Integration: send_message refuses BEFORE any nav ────────────────


@pytest.mark.asyncio
async def test_send_message_blocks_url_without_navigation(monkeypatch):
    """Critical: when a URL is detected, send_message must return
    early WITHOUT calling get_browser() / safe_goto(). Verifies by
    monkeypatching get_browser to a sentinel that raises if touched."""
    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("get_browser should NOT have been called")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="check my portfolio at github.com/Bsh13lder/Lazy-Claw",
    ))
    assert result["status"] == "blocked"
    assert "github.com" in result["offending_token"].lower()
    assert called["hit"] is False, "browser must NOT be touched on URL block"


@pytest.mark.asyncio
async def test_send_message_blocks_product_pitch_without_navigation(monkeypatch):
    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("get_browser should NOT have been called")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="LazyClaw runs an undetectable browser via CDP",
    ))
    assert result["status"] == "blocked"
    assert "lazyclaw" in result["offending_token"].lower()
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_send_message_url_check_runs_before_product_check(monkeypatch):
    """When a message has BOTH problems, URL is reported first — it's
    the more concrete violation Upwork's filter will hit immediately."""

    def _trip_wire(*_a, **_kw):
        raise RuntimeError("must not navigate")
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="LazyClaw at https://github.com/Bsh13lder/Lazy-Claw",
    ))
    assert result["status"] == "blocked"
    # URL guard fires first
    assert "github" in result["offending_token"].lower() or \
           "https" in result["offending_token"].lower()
