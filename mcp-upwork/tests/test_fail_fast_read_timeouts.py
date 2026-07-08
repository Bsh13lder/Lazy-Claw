"""Fail-fast tests for the Upwork read-tool 60s-timeout incident (2026-07-03).

Live incident: ``upwork_get_conversation``, ``upwork_get_unread_count``,
and ``upwork_get_messages`` all timed out ~60s against a genuinely-loaded
(no Cloudflare) Upwork Messages tab. mcp-upwork's own stderr showed the
room/bubble selectors matching ZERO elements — Upwork's 2026 layout
apparently renamed the ``[data-test="room-item"]`` / rooms-panel hooks —
but there was no fail-fast: the code burned its FULL wait budget on
elements that were never going to appear.

Two stacked root causes, both verifiable from the code (not guesses):

1. ``browser/client.py:safe_goto`` defaults ``wait_until="networkidle"``.
   Upwork's messages page holds an open WebSocket/long-poll connection
   for real-time delivery — the code's OWN comment at
   ``tools/messages.py`` (search "Upwork's SPA frequently never hits
   networkidle") already documents that this page never goes network-
   idle. Every ``page.goto()``/``page.reload()`` with this wait_until
   burns the FULL per-page navigation timeout (default 30000ms) waiting
   for a network state that will never happen.
   ``get_conversation_messages`` passes ``force_reload=True`` — that's
   TWO such waits (goto + reload) = up to 60000ms from navigation ALONE,
   before any selector is even queried. This is an exact match for the
   reported "~60s" symptom on ``upwork_get_conversation``.

2. ``get_messages``' rooms-panel ``wait_for_selector`` used a flat
   20000ms timeout with no short-circuit when NONE of the 4 candidate
   selectors will ever match (structural layout drift). Combined with
   the ~30s navigation risk above plus the trailing bounded
   ``networkidle``/stability polls, total wall time crept to ~58-60s.

Fix under test: message-page navigations pass a fast, deterministic
``wait_until`` (not the network-idle-forever default), and the rooms-
panel selector wait is capped to a short, bounded ceiling — converting
"wait the full budget for elements that will never appear" into "fail
fast with a clear structured result."

These are unit-level contract tests (assert on the ARGUMENTS passed to
the browser primitives + the final structured result), matching this
repo's existing test style (see test_safe_goto_room_check.py). They
don't — and can't — prove live Upwork DOM timing; that requires a live
capture (see fix-upwork-report.md "needs live-DOM confirmation").
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from upwork_mcp.tools import messages as messages_mod
from upwork_mcp.tools.messages import (
    MessagesParams,
    _FAST_NAV_WAIT_UNTIL,
    _ROOMS_PANEL_WAIT_MS,
)


@pytest.fixture(autouse=True)
def _no_real_sleeps():
    """These tests assert on TIMEOUT ARGUMENTS, not real elapsed time —
    matches tests/test_empty_vs_blocked.py's convention of stubbing
    ``asyncio.sleep`` so the bounded stability-poll loops in
    ``get_messages`` / ``get_conversation_messages`` don't burn real
    wall-clock seconds per test."""
    with patch("asyncio.sleep", new=AsyncMock()):
        yield


# ─── shared fakes (mirrors tests/test_empty_vs_blocked.py's FakePage) ────


class FakePage:
    """Minimal Playwright-page stand-in that records every wait/goto
    call's arguments so tests can assert on the TIMEOUT BUDGET rather
    than needing to actually sleep for it."""

    def __init__(
        self,
        url: str = "https://www.upwork.com/ab/messages/rooms/",
        title: str = "Messages | Upwork",
        content: str = "<html><body>messages</body></html>",
    ):
        self.url = url
        self._title = title
        self._content = content
        self.goto_calls: list[tuple[str, dict]] = []
        self.reload_calls: list[dict] = []
        self.wait_for_selector_calls: list[tuple[str, int | None]] = []

    async def title(self):
        return self._title

    async def content(self):
        return self._content

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    async def reload(self, **kwargs):
        self.reload_calls.append(kwargs)

    async def query_selector(self, selector):
        return None

    async def query_selector_all(self, selector):
        return []

    async def wait_for_selector(self, selector, timeout=None):
        self.wait_for_selector_calls.append((selector, timeout))
        raise TimeoutError(f"no element matched: {selector}")

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def evaluate(self, js, *a, **kw):
        return {"scrolled": True, "count": 0}

    def is_closed(self):
        return False


def _fake_browser(page: FakePage) -> MagicMock:
    browser = MagicMock()
    browser.ensure_logged_in = AsyncMock(return_value=True)
    browser.get_page = AsyncMock(return_value=page)
    browser.safe_goto = AsyncMock(return_value=page)
    return browser


# ─── root cause 1: navigation must not default to networkidle ───────────


@pytest.mark.asyncio
async def test_get_messages_safe_goto_uses_fast_wait_until(monkeypatch):
    """``get_messages`` must override safe_goto's networkidle-forever
    default — Upwork's messages page never idles (open WS connection)."""
    page = FakePage()
    browser = _fake_browser(page)
    monkeypatch.setattr(messages_mod, "_load_seen_rooms", lambda: set())
    monkeypatch.setattr(messages_mod, "get_browser", lambda: browser)

    await messages_mod.get_messages(MessagesParams())

    assert browser.safe_goto.await_count == 1
    _, kwargs = browser.safe_goto.await_args
    assert kwargs.get("wait_until") == _FAST_NAV_WAIT_UNTIL
    assert kwargs.get("wait_until") != "networkidle"


@pytest.mark.asyncio
async def test_get_conversation_messages_safe_goto_uses_fast_wait_until(
    monkeypatch,
):
    """``get_conversation_messages`` passes ``force_reload=True`` — that's
    TWO networkidle-forever waits (goto + reload) if left unfixed, i.e.
    up to 60000ms from navigation alone. Must use the fast wait_until
    while STILL forcing the reload (freshness requirement is unrelated
    to this fix and must be preserved)."""
    page = FakePage(
        url="https://www.upwork.com/ab/messages/rooms/room_abc123",
        title="James Blue",
    )
    browser = _fake_browser(page)
    monkeypatch.setattr(messages_mod, "get_browser", lambda: browser)

    await messages_mod.get_conversation_messages("room_abc123")

    assert browser.safe_goto.await_count == 1
    _, kwargs = browser.safe_goto.await_args
    assert kwargs.get("wait_until") == _FAST_NAV_WAIT_UNTIL
    assert kwargs.get("wait_until") != "networkidle"
    # Freshness-forcing reload must NOT regress while fixing the timeout.
    assert kwargs.get("force_reload") is True


@pytest.mark.asyncio
async def test_get_unread_count_safe_goto_uses_fast_wait_until(monkeypatch):
    page = FakePage(url="https://www.upwork.com/nx/find-work/")
    browser = _fake_browser(page)
    monkeypatch.setattr(messages_mod, "get_browser", lambda: browser)

    await messages_mod.get_unread_count()

    assert browser.safe_goto.await_count == 1
    _, kwargs = browser.safe_goto.await_args
    assert kwargs.get("wait_until") == _FAST_NAV_WAIT_UNTIL
    assert kwargs.get("wait_until") != "networkidle"


# ─── root cause 2: rooms-panel wait must be short + bounded ─────────────


@pytest.mark.asyncio
async def test_get_messages_rooms_panel_wait_is_short_and_bounded(
    monkeypatch,
):
    """The old flat 20000ms wait_for_selector timeout is THE reason a
    genuinely-empty/drifted rooms panel burned most of the 60s budget.
    Must be capped well under the old value — "a few seconds, not 60s".
    """
    page = FakePage()
    browser = _fake_browser(page)
    monkeypatch.setattr(messages_mod, "_load_seen_rooms", lambda: set())
    monkeypatch.setattr(messages_mod, "get_browser", lambda: browser)

    await messages_mod.get_messages(MessagesParams())

    assert len(page.wait_for_selector_calls) == 1
    _, timeout = page.wait_for_selector_calls[0]
    assert timeout == _ROOMS_PANEL_WAIT_MS
    # The old hang was 20000ms for this wait alone; must be materially
    # shorter — "a few seconds", not most of a minute.
    assert timeout <= 8000


# ─── end-to-end: zero rooms/bubbles still yields a fast, honest result ──


@pytest.mark.asyncio
async def test_get_messages_zero_rooms_fails_fast_with_structured_result(
    monkeypatch,
):
    """The exact reported shape: rooms-panel selector times out, ZERO
    room elements match, no empty-state marker either (Upwork didn't
    render one — layout drift, not a genuinely empty inbox). The call
    must still return the structured empty/blocked dict — not hang —
    and must have spent a bounded, short timeout doing so."""
    page = FakePage()
    browser = _fake_browser(page)
    monkeypatch.setattr(messages_mod, "_load_seen_rooms", lambda: set())
    monkeypatch.setattr(messages_mod, "get_browser", lambda: browser)

    result = await messages_mod.get_messages(MessagesParams())

    assert isinstance(result, dict)
    assert result["status"] == "empty_or_blocked"
    assert result["items"] == []
    # Proves the fail-fast path was taken, not a lucky fast return.
    _, timeout = page.wait_for_selector_calls[0]
    assert timeout <= 8000


@pytest.mark.asyncio
async def test_get_conversation_messages_zero_bubbles_fails_fast(
    monkeypatch,
):
    """Mirrors the ``upwork_get_conversation`` incident: room page loads,
    zero story-container/legacy/widened bubbles match, zero a11y fallback
    (page not recognized as "on a room" in this fake). Must return an
    empty messages list fast rather than hang on navigation."""
    page = FakePage(
        url="https://www.upwork.com/ab/messages/rooms/room_abc123",
        title="James Blue",
    )
    browser = _fake_browser(page)
    monkeypatch.setattr(messages_mod, "get_browser", lambda: browser)

    result = await messages_mod.get_conversation_messages("room_abc123")

    assert isinstance(result, dict)
    assert result.get("messages") == []
    # The fast wait_until was actually used for this call (not the
    # networkidle-forever default) — ties correctness to the timing fix.
    _, kwargs = browser.safe_goto.await_args
    assert kwargs.get("wait_until") == _FAST_NAV_WAIT_UNTIL
