"""Unit tests for the ``safe_goto`` post-nav room-id check + the
structured ``room_not_found`` error returned by ``get_conversation_messages``
when the SPA silently redirects.

Live incident 2026-05-20 16:48:45–16:49:18: brain navigated to
``/ab/messages/rooms/9992d280-e432-470c-9c31-798aa07fcb1e`` and Upwork's
SPA silently redirected it to ``/ab/messages`` (the "Looking for
something?" 404 surface). The old ``safe_goto`` never compared
``page.url`` after ``page.goto`` so the bubble extractor scraped the
inbox as if it were the requested thread.

The defense:
  1. After CF-pass, if the INPUT url named a room, verify the final
     ``page.url`` still contains the expected id.
  2. If not, raise ``UpworkRoomNotFound(requested_url, final_url)``.
  3. ``get_conversation_messages`` (and ``send_message`` / ``edit_message``)
     catch the exception and return a structured dict with
     ``error: "room_not_found"`` so the brain gets a groundable signal
     instead of a 500 bubbling up to MCP.

No Playwright runtime — uses small fakes that mimic the ``page.goto`` +
``page.url`` + ``page.content`` surface ``safe_goto`` exercises.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from upwork_mcp.browser.client import (
    UpworkBrowser,
    UpworkRoomNotFound,
    _extract_expected_room_id,
)
from upwork_mcp.tools.messages import (
    SendMessageParams,
    EditMessageParams,
    get_conversation_messages,
    send_message,
    edit_message,
)


# ── _extract_expected_room_id parser ────────────────────────────────


@pytest.mark.parametrize("url,expected", [
    # room_<hex> shape (the primary 2026 layout)
    (
        "https://www.upwork.com/ab/messages/rooms/room_abc123def",
        "room_abc123def",
    ),
    # room_<hex> with query string
    (
        "https://www.upwork.com/ab/messages/rooms/room_abc123"
        "?companyReference=~01x&sidebar=true",
        "room_abc123",
    ),
    # UUID shape
    (
        "https://www.upwork.com/ab/messages/rooms/"
        "9992d280-e432-470c-9c31-798aa07fcb1e",
        "9992d280-e432-470c-9c31-798aa07fcb1e",
    ),
    # Legacy /nx/messages/<numeric>
    (
        "https://www.upwork.com/nx/messages/12345",
        "12345",
    ),
])
def test_extract_expected_room_id_matches_known_shapes(url, expected):
    assert _extract_expected_room_id(url) == expected


@pytest.mark.parametrize("url", [
    # Bare inbox URL — no specific room id
    "https://www.upwork.com/ab/messages/rooms",
    "https://www.upwork.com/ab/messages/rooms/",
    "https://www.upwork.com/ab/messages/rooms?filter=unread",
    # Non-message URLs — exempt from the check
    "https://www.upwork.com/nx/find-work/",
    "https://www.upwork.com/nx/find-work/best-matches",
    "https://www.upwork.com/freelancers/~01abc",
    # Garbage / empty
    "",
    None,
])
def test_extract_expected_room_id_returns_none_for_non_room_urls(url):
    assert _extract_expected_room_id(url) is None


# ── safe_goto behavior ──────────────────────────────────────────────


class FakePage:
    """Minimal Playwright Page surrogate.

    Tracks the ``page.url`` ``safe_goto`` checks AFTER goto + CF pass.
    Set ``url_after_goto`` to simulate Upwork's SPA redirecting (e.g.
    invalid room → /ab/messages).
    """

    def __init__(self, *, url_before: str, url_after: str, body: str = ""):
        self._url_before = url_before
        self._url_after = url_after
        self._body = body
        # Track which url we're currently reporting via ``page.url``
        self._current_url = url_before
        self.goto_calls: list[tuple[str, dict]] = []
        self.reload_calls: list[dict] = []

    @property
    def url(self) -> str:
        return self._current_url

    def is_closed(self) -> bool:
        return False

    def set_default_timeout(self, *_a, **_kw) -> None:
        return None

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append((url, kwargs))
        # After goto completes, ``page.url`` flips to whatever the SPA
        # landed on. Real Playwright does this; we mimic it.
        self._current_url = self._url_after

    async def reload(self, **kwargs) -> None:
        self.reload_calls.append(kwargs)

    async def content(self) -> str:
        return self._body

    async def title(self) -> str:
        return ""


@pytest.fixture
def patched_browser(monkeypatch):
    """Build an ``UpworkBrowser`` with ``get_page`` short-circuited to a
    caller-supplied ``FakePage`` so ``safe_goto`` runs end-to-end without
    a real CDP connection.

    Returns a factory: ``patched_browser(fake_page)`` → configured browser.
    """
    def _factory(fake_page: FakePage) -> UpworkBrowser:
        browser = UpworkBrowser(headless=True, timeout=5000)

        async def _get_page():
            return fake_page

        monkeypatch.setattr(browser, "get_page", _get_page)
        return browser

    return _factory


@pytest.mark.asyncio
async def test_safe_goto_passes_when_room_id_in_final_url(patched_browser):
    """Happy path: ``page.url`` after goto still contains the requested
    ``room_<hex>``. ``safe_goto`` returns the page without raising.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/room_abc123"
        "?companyReference=~01x&sidebar=true"
    )
    final = "https://www.upwork.com/ab/messages/rooms/room_abc123"
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=final,
    )
    browser = patched_browser(fake_page)

    page = await browser.safe_goto(requested, warm=False)
    assert page is fake_page
    # goto was actually called
    assert len(fake_page.goto_calls) == 1
    assert fake_page.goto_calls[0][0] == requested


@pytest.mark.asyncio
async def test_safe_goto_raises_when_redirected_to_inbox(patched_browser):
    """The exact 2026-05-20 16:48:47 failure shape: requested URL has a
    UUID room id; SPA lands on ``/ab/messages``. ``safe_goto`` MUST
    raise ``UpworkRoomNotFound`` carrying both URLs.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/"
        "9992d280-e432-470c-9c31-798aa07fcb1e"
    )
    final = "https://www.upwork.com/ab/messages"
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=final,
    )
    browser = patched_browser(fake_page)

    with pytest.raises(UpworkRoomNotFound) as excinfo:
        await browser.safe_goto(requested, warm=False)
    assert excinfo.value.requested_url == requested
    assert excinfo.value.final_url == final
    # Both URLs appear in the message so logs are self-contained
    assert "9992d280" in str(excinfo.value)
    assert "/ab/messages" in str(excinfo.value)


@pytest.mark.asyncio
async def test_safe_goto_skips_check_for_inbox_url(patched_browser):
    """Passing ``/ab/messages`` itself MUST NOT trigger the room-id
    check — it's a legitimate inbox nav, not a per-room nav.

    Same applies to non-room URLs like ``/nx/find-work/``.
    """
    requested = "https://www.upwork.com/ab/messages/rooms"
    final = "https://www.upwork.com/ab/messages/rooms"
    fake_page = FakePage(
        url_before="https://www.upwork.com/",
        url_after=final,
    )
    browser = patched_browser(fake_page)

    # Should NOT raise — inbox URL has no expected room id to compare
    page = await browser.safe_goto(requested, warm=False)
    assert page is fake_page


@pytest.mark.asyncio
async def test_safe_goto_raises_when_loose_id_redirected(patched_browser):
    """Non-standard room ids (anything after ``/ab/messages/rooms/``
    that doesn't match the strict ``room_<hex>`` / UUID / numeric
    patterns) must STILL trigger the post-nav check via the loose
    fallback regex. Real-world failure: brain confabulated a contact
    name into the room-id slot — ``/ab/messages/rooms/James%20Blue``
    — Upwork's SPA redirected to ``/ab/messages`` (the 404 surface),
    and without the loose fallback the post-nav check was SKIPPED
    because ``James%20Blue`` doesn't match the strict regex.

    With the loose fallback, the expected id becomes ``James%20Blue``
    and the equality check fires correctly.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/James%20Blue"
    )
    final = "https://www.upwork.com/ab/messages"
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=final,
    )
    browser = patched_browser(fake_page)

    with pytest.raises(UpworkRoomNotFound) as excinfo:
        await browser.safe_goto(requested, warm=False)
    assert excinfo.value.requested_url == requested
    assert excinfo.value.final_url == final
    # The loose-form expected id is the raw slug after /rooms/
    assert "James%20Blue" in excinfo.value.requested_url


@pytest.mark.asyncio
async def test_safe_goto_raises_when_room_hex_redirected_to_inbox(
    patched_browser,
):
    """Same failure shape but with the ``room_<hex>`` URL variant — the
    parser must catch both shapes the extractor produces.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/room_deadbeef"
        "?sidebar=true"
    )
    final = "https://www.upwork.com/ab/messages"
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=final,
    )
    browser = patched_browser(fake_page)

    with pytest.raises(UpworkRoomNotFound) as excinfo:
        await browser.safe_goto(requested, warm=False)
    assert "room_deadbeef" in excinfo.value.requested_url
    assert excinfo.value.final_url == final


# ── messages.py wraps the exception into a structured dict ──────────


@pytest.mark.asyncio
async def test_get_conversation_messages_returns_structured_error_on_room_not_found(
    monkeypatch,
):
    """``get_conversation_messages`` must translate ``UpworkRoomNotFound``
    into a ``{"error": "room_not_found", "requested": ..., "redirected_to": ...}``
    dict instead of bubbling the exception up to MCP as a 500.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/"
        "9992d280-e432-470c-9c31-798aa07fcb1e"
    )
    final = "https://www.upwork.com/ab/messages"

    # Fake browser whose ``safe_goto`` raises the typed exception.
    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=True)
    fake_browser.safe_goto = AsyncMock(
        side_effect=UpworkRoomNotFound(
            requested_url=requested, final_url=final,
        )
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )

    result = await get_conversation_messages(room_id=requested)
    assert isinstance(result, dict)
    assert result["error"] == "room_not_found"
    assert result["requested"] == requested
    assert result["redirected_to"] == final
    # No bubble fields leaked from a different conversation
    assert "messages" not in result


@pytest.mark.asyncio
async def test_send_message_returns_structured_error_on_room_not_found(
    monkeypatch,
):
    """Same defense on the write side — refuse to type a draft into the
    wrong conversation, return the structured error instead.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/"
        "9992d280-e432-470c-9c31-798aa07fcb1e"
    )
    final = "https://www.upwork.com/ab/messages"

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=True)
    fake_browser.safe_goto = AsyncMock(
        side_effect=UpworkRoomNotFound(
            requested_url=requested, final_url=final,
        )
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )

    # Plain text that survives the URL + product-pitch guards so we
    # actually reach safe_goto and exercise the room-not-found branch.
    result = await send_message(SendMessageParams(
        room_id=requested,
        message="thanks for the quick reply",
    ))
    assert isinstance(result, dict)
    assert result["error"] == "room_not_found"
    assert result["requested"] == requested
    assert result["redirected_to"] == final


@pytest.mark.asyncio
async def test_edit_message_returns_structured_error_on_room_not_found(
    monkeypatch,
):
    """Same defense on the edit path."""
    requested = (
        "https://www.upwork.com/ab/messages/rooms/"
        "9992d280-e432-470c-9c31-798aa07fcb1e"
    )
    final = "https://www.upwork.com/ab/messages"

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=True)
    fake_browser.safe_goto = AsyncMock(
        side_effect=UpworkRoomNotFound(
            requested_url=requested, final_url=final,
        )
    )
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )

    result = await edit_message(EditMessageParams(
        room_id=requested,
        message_index=0,
        new_content="corrected text",
    ))
    assert isinstance(result, dict)
    assert result["error"] == "room_not_found"
    assert result["requested"] == requested
    assert result["redirected_to"] == final


# ── force_reload: defeat SPA + Service Worker DOM caching ───────────
#
# Root cause 2026-05-24: ``safe_goto`` issued ``page.goto(url)`` then
# fell through to scroll-to-bottom + render-stability extraction. When
# the picked tab was ALREADY on the same room URL, Playwright's goto
# becomes a soft navigation — the React SPA detects same-URL routing,
# skips the data re-fetch, and Upwork's Service Worker (registered at
# /ab/messages/sw.js) serves the page entry-point from cache. Result:
# DOM is "stable" in its frozen state from when the tab originally
# loaded, the extractor returns the same bubbles forever, the brain
# reports identical "10:37 PM" timestamps on every call hours apart.
#
# Live evidence: two Brave tabs sitting on the SAME room URL at audit
# time; identical message content returned at 2:32 PM and 3:31 PM on
# 2026-05-24 even though the contract is 5 days past due and James
# may have actually replied.
#
# Fix: opt-in ``force_reload=True`` on ``safe_goto`` issues a hard
# ``page.reload`` AFTER goto + CF pass. Reload is distinct from goto
# at the browser layer — it bypasses the SPA's same-URL short-circuit
# AND signals the Service Worker that this is a fresh fetch.


@pytest.mark.asyncio
async def test_safe_goto_does_not_reload_by_default(patched_browser):
    """Default behavior MUST be backwards compatible — no reload call.
    Otherwise every read-only nav (proposal browse, job search, profile
    open) would pay an unnecessary cost and risk re-triggering CF.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/room_abc123"
    )
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=requested,
    )
    browser = patched_browser(fake_page)

    await browser.safe_goto(requested, warm=False)
    assert len(fake_page.goto_calls) == 1
    assert fake_page.reload_calls == [], (
        "safe_goto with force_reload=False (default) must NOT call "
        "page.reload — backward-compat for all non-message nav"
    )


@pytest.mark.asyncio
async def test_safe_goto_forces_reload_when_force_reload_true(patched_browser):
    """Smoking-gun fix: with ``force_reload=True``, ``safe_goto`` MUST
    call ``page.reload`` after the goto + CF pass so the SPA + Service
    Worker re-fetch the page entry-point. Without this, the message
    extractor downstream reads stale cached DOM bubbles.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/room_abc123"
    )
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=requested,
    )
    browser = patched_browser(fake_page)

    await browser.safe_goto(requested, warm=False, force_reload=True)
    assert len(fake_page.goto_calls) == 1, (
        "goto still runs first to establish URL + cookie context"
    )
    assert len(fake_page.reload_calls) == 1, (
        "force_reload=True must trigger exactly one page.reload AFTER goto "
        "— this is the defeat path for Upwork's SPA same-URL short-circuit "
        "and the /ab/messages/sw.js Service Worker cache"
    )


@pytest.mark.asyncio
async def test_safe_goto_force_reload_runs_room_id_check_after_reload(
    patched_browser,
):
    """The post-nav room-id check MUST still fire after a reload — a
    reload could itself trigger Upwork's silent redirect (e.g. the
    room was deleted between goto and reload). The structured
    ``UpworkRoomNotFound`` defense must remain intact under reload.
    """
    requested = (
        "https://www.upwork.com/ab/messages/rooms/room_deadbeef"
    )
    # Tab landed on the requested URL after goto, but then the reload
    # surfaces a redirect to /ab/messages (room deleted server-side).
    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=requested,
    )
    # Mutate url after reload to simulate the redirect
    original_reload = fake_page.reload

    async def reload_then_redirect(**kwargs):
        await original_reload(**kwargs)
        fake_page._current_url = "https://www.upwork.com/ab/messages"

    fake_page.reload = reload_then_redirect
    browser = patched_browser(fake_page)

    with pytest.raises(UpworkRoomNotFound) as excinfo:
        await browser.safe_goto(
            requested, warm=False, force_reload=True,
        )
    assert excinfo.value.requested_url == requested
    assert excinfo.value.final_url == "https://www.upwork.com/ab/messages"


@pytest.mark.asyncio
async def test_get_conversation_messages_calls_safe_goto_with_force_reload(
    monkeypatch,
):
    """Wire-up assertion: the message-fetch path MUST request
    ``force_reload=True`` so the bug above doesn't reappear. This is
    the contract between the messages tool and ``safe_goto`` — without
    it, the staleness bug returns silently.
    """
    captured_kwargs: dict = {}

    fake_page = FakePage(
        url_before="https://www.upwork.com/nx/find-work/",
        url_after=(
            "https://www.upwork.com/ab/messages/rooms/room_abc123"
        ),
    )

    async def fake_safe_goto(url: str, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["url"] = url
        # Raise after capturing — we only care about the call signature,
        # not the downstream extraction.
        raise UpworkRoomNotFound(
            requested_url=url, final_url=url,
        )

    fake_browser = MagicMock()
    fake_browser.ensure_logged_in = AsyncMock(return_value=True)
    fake_browser.safe_goto = AsyncMock(side_effect=fake_safe_goto)
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: fake_browser
    )

    await get_conversation_messages(
        room_id="room_abc123",
    )
    # The structured error returns; what we care about is the call args.
    fake_browser.safe_goto.assert_called_once()
    _args, kwargs = fake_browser.safe_goto.call_args
    assert kwargs.get("force_reload") is True, (
        "get_conversation_messages MUST pass force_reload=True or the "
        "stale-DOM bug from 2026-05-24 reappears"
    )


# ── Chat scroller finder ─ DOM-drift defense ────────────────────────
#
# Root cause 2026-05-24 (caught via live CDP probe): the scroll-to-
# bottom + scroll-up-iteration JS in messages.py declared 5 class-based
# selectors, ALL of which returned NULL on Upwork's current 2026 DOM.
# The real chat scroller ships as ``<div class="scroll-wrapper
# custom-scrollbar">``. With no scroller found, the overflow-based
# fallback also failed because custom-scrollbar wrappers use
# ``overflow: hidden`` with faked scrollbars. Net effect: scroll was
# a silent no-op, virtualized chat stayed at scrollTop=0 (oldest
# messages mounted), bubble extractor returned 5-day-old bubbles even
# with force_reload=True loading fresh DOM. Brain reported "James
# hasn't replied" when he had replied THAT MORNING.
#
# The fix: shared ``_FIND_CHAT_SCROLLER_JS`` constant used by BOTH
# scroll sites, with three strategies (class-based → content-based
# walking up from a bubble → overflow-based legacy fallback).


def test_find_chat_scroller_js_includes_2026_class_selector():
    """The fix MUST include ``.scroll-wrapper.custom-scrollbar`` — the
    real Upwork 2026 chat scroller class verified via live CDP probe
    2026-05-24. Without this selector in the cheap fast-path, the
    extractor falls through to the slow content-based walk, which is
    correct but ~20× slower per call.
    """
    from upwork_mcp.tools.messages import _FIND_CHAT_SCROLLER_JS
    assert ".scroll-wrapper.custom-scrollbar" in _FIND_CHAT_SCROLLER_JS, (
        "Cheap class-selector fast path MUST include Upwork's 2026 "
        "chat scroller class — without it, scroll-to-bottom relies "
        "on the content-based fallback only"
    )
    assert ".scroll-wrapper" in _FIND_CHAT_SCROLLER_JS, (
        "Bare .scroll-wrapper kept as second-tier fallback for future "
        "DOM tweaks (Upwork dropping the .custom-scrollbar suffix)"
    )


def test_find_chat_scroller_js_has_content_based_fallback():
    """Class selectors will rot — Upwork ships layout changes weekly.
    The content-based walk-up from ``[data-test="story-container"]``
    must remain as the DOM-drift defense. If a future refactor removes
    it, this test catches the regression before the bug reappears.
    """
    from upwork_mcp.tools.messages import _FIND_CHAT_SCROLLER_JS
    assert 'data-test="story-container"' in _FIND_CHAT_SCROLLER_JS, (
        "Content-based scroller walk MUST anchor on the bubble "
        "selector — this is the DOM-rename-survival path"
    )
    # And confirm both scroll sites use the shared helper, so future
    # selector changes only need to happen in one place.
    import upwork_mcp.tools.messages as messages_mod
    import inspect
    src = inspect.getsource(messages_mod)
    # Count occurrences of the old hardcoded selectors — must be ZERO
    # outside the constant itself (no inline duplicate selector lists).
    old_selector = "div[class*=\"MessageList\"]"
    occurrences = src.count(old_selector)
    assert occurrences == 1, (
        f"Hardcoded scroller selector list found in {occurrences} "
        f"places — must live in _FIND_CHAT_SCROLLER_JS only so the "
        f"2026-05-24 staleness bug doesn't return"
    )
