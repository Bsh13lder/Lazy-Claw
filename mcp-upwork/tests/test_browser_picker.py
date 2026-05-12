"""Unit tests for _pick_upwork_page + _is_nav_noise + _looks_like_upwork.

No Playwright runtime — pure mock objects. These helpers are the core
fix for the Cloudflare-block + Target-closed family of bugs: pick an
Upwork tab over a generic one so cookies + warm session pass CF.
"""

from __future__ import annotations

import pytest

from upwork_mcp.browser.client import (
    _is_nav_noise,
    _looks_like_upwork,
    _pick_upwork_page,
)


class FakePage:
    def __init__(self, url: str, closed: bool = False) -> None:
        self.url = url
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages


class FakeBrowser:
    def __init__(self, contexts: list[FakeContext]) -> None:
        self.contexts = contexts


# ── _is_nav_noise ───────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "Settings", "settings", " Settings ",
    "Edit Profile", "My Profile", "Messages",
    "Find Work", "Best Matches", "Proposals", "Contracts",
    "Skip skills", "Previous skills. Update list",
])
def test_is_nav_noise_matches_known_labels(value):
    assert _is_nav_noise(value) is True


@pytest.mark.parametrize("value", [
    "Vato T.", "Python Backend Developer", "FastAPI",
    "I will build your AI agent", "REST API",
])
def test_is_nav_noise_passes_real_data(value):
    assert _is_nav_noise(value) is False


# ── _looks_like_upwork ──────────────────────────────────────────────


def test_looks_like_upwork_true_for_upwork_url():
    assert _looks_like_upwork(FakePage("https://www.upwork.com/ab/messages/")) is True


def test_looks_like_upwork_false_for_other_origin():
    assert _looks_like_upwork(FakePage("https://www.youtube.com/")) is False


def test_looks_like_upwork_false_for_closed_page():
    assert _looks_like_upwork(FakePage("https://www.upwork.com/", closed=True)) is False


def test_looks_like_upwork_false_for_empty_url():
    assert _looks_like_upwork(FakePage("")) is False


# ── _pick_upwork_page ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pick_upwork_page_returns_upwork_tab_over_other_origin():
    """If a YouTube tab and an Upwork tab are open, pick Upwork."""
    youtube = FakePage("https://www.youtube.com/watch?v=abc")
    upwork = FakePage("https://www.upwork.com/nx/find-work/")
    ctx = FakeContext([youtube, upwork])
    browser = FakeBrowser([ctx])

    picked = await _pick_upwork_page(browser)
    assert picked is not None
    assert picked[0] is ctx
    assert picked[1] is upwork


@pytest.mark.asyncio
async def test_pick_upwork_page_avoids_room_url_when_other_upwork_tab_exists():
    """When two Upwork tabs exist and one is the inbox-room (where the
    user is reading a conversation), prefer the non-room tab so we don't
    navigate the user away from their open thread."""
    room_tab = FakePage("https://www.upwork.com/ab/messages/rooms/room_abc123?sidebar=true")
    inbox_tab = FakePage("https://www.upwork.com/nx/find-work/")
    ctx = FakeContext([room_tab, inbox_tab])
    browser = FakeBrowser([ctx])

    picked = await _pick_upwork_page(browser)
    assert picked is not None
    assert picked[1] is inbox_tab  # not the room tab


@pytest.mark.asyncio
async def test_pick_upwork_page_returns_room_tab_when_only_upwork_option():
    """When the ONLY upwork tab open is a room URL, still return it —
    don't fall back to a non-upwork tab just to avoid the room."""
    room_tab = FakePage("https://www.upwork.com/ab/messages/rooms/room_abc123")
    other = FakePage("https://www.youtube.com/")
    ctx = FakeContext([room_tab, other])
    browser = FakeBrowser([ctx])

    picked = await _pick_upwork_page(browser)
    assert picked is not None
    assert picked[1] is room_tab


@pytest.mark.asyncio
async def test_pick_upwork_page_falls_back_when_no_upwork_tab():
    """Zero Upwork tabs open — return the first non-closed page so the
    caller can warm-navigate it to upwork.com."""
    youtube = FakePage("https://www.youtube.com/")
    twitter = FakePage("https://twitter.com/home")
    ctx = FakeContext([youtube, twitter])
    browser = FakeBrowser([ctx])

    picked = await _pick_upwork_page(browser)
    assert picked is not None
    assert picked[1] is youtube  # the fallback is the first non-closed


@pytest.mark.asyncio
async def test_pick_upwork_page_returns_none_when_all_closed():
    """Every tab is closed (rare but possible after a hard CDP reset)."""
    closed = FakePage("https://www.upwork.com/", closed=True)
    ctx = FakeContext([closed])
    browser = FakeBrowser([ctx])

    picked = await _pick_upwork_page(browser)
    assert picked is None


@pytest.mark.asyncio
async def test_pick_upwork_page_walks_multiple_contexts():
    """Upwork tab is in a different BrowserContext than the others —
    we still find it (cookies are shared at the browser level)."""
    yt_ctx = FakeContext([FakePage("https://www.youtube.com/")])
    upwork_ctx = FakeContext([FakePage("https://www.upwork.com/nx/find-work/")])
    browser = FakeBrowser([yt_ctx, upwork_ctx])

    picked = await _pick_upwork_page(browser)
    assert picked is not None
    assert picked[0] is upwork_ctx


@pytest.mark.asyncio
async def test_pick_upwork_page_handles_empty_browser():
    browser = FakeBrowser([])
    picked = await _pick_upwork_page(browser)
    assert picked is None
