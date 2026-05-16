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


# ── draft_only: type into compose, do NOT click Send ────────────────


class _FakeInputEl:
    """Stands in for the Tiptap contenteditable element."""
    def __init__(self):
        self.clicked = False
        self.text_typed: list[str] = []
        self.pressed_keys: list[str] = []

    async def evaluate(self, _js: str) -> str:
        return "DIV"  # contenteditable, not a textarea

    async def click(self) -> None:
        self.clicked = True

    async def press(self, key: str) -> None:
        self.pressed_keys.append(key)

    async def text_content(self) -> str:
        return "".join(self.text_typed)

    async def input_value(self) -> str:
        return "".join(self.text_typed)


class _FakeKeyboard:
    def __init__(self, store: list[str]):
        self._store = store

    async def type(self, text: str, *, delay: int = 0) -> None:
        self._store.append(text)


class _FakePage:
    def __init__(self):
        self.input_el = _FakeInputEl()
        self.keyboard = _FakeKeyboard(self.input_el.text_typed)
        self.send_button_clicked = False

    async def query_selector(self, selector: str):
        if "Send" in selector or "send" in selector or "submit" in selector:
            # Track that the caller LOOKED for the send button — but we
            # need to verify it was NOT clicked when draft_only=True.
            class _SendBtn:
                async def click(_s):
                    raise AssertionError(
                        "Send button must NOT be clicked in draft_only mode"
                    )
            return _SendBtn()
        return self.input_el


class _FakeBrowser:
    def __init__(self):
        self.page = _FakePage()
        self.ensure_logged_in_called = False
        self.safe_goto_url: str | None = None

    async def ensure_logged_in(self):
        self.ensure_logged_in_called = True

    async def safe_goto(self, url: str):
        self.safe_goto_url = url
        return self.page


@pytest.mark.asyncio
async def test_send_message_draft_only_does_not_send(monkeypatch):
    """draft_only=True types into the compose box but skips the Send
    button. The fake send button's .click() raises if it's ever called."""
    browser = _FakeBrowser()
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: browser
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="Hello James, here's a draft for your review.",
        draft_only=True,
    ))

    assert result["status"] == "drafted"
    assert "review" in result["message"].lower() or "manually" in result["message"].lower()
    assert result["room_id"] == "room_abc"
    assert result["char_count"] == len(
        "Hello James, here's a draft for your review."
    )
    # The fake page raises if Send is clicked — reaching here proves it wasn't
    assert browser.ensure_logged_in_called
    assert browser.safe_goto_url is not None
    # Text WAS typed into the box
    assert browser.page.input_el.text_typed == [
        "Hello James, here's a draft for your review."
    ]


@pytest.mark.asyncio
async def test_send_message_default_still_sends(monkeypatch):
    """Back-compat: omitting draft_only (or passing False) preserves
    the original send-and-verify behavior. Send button click happens."""
    browser = _FakeBrowser()

    # Override the send-button query to a click-tracking stub
    real_query = browser.page.query_selector

    class _RecordingSendBtn:
        def __init__(self, page):
            self._page = page
            page.send_button_clicked = False
        async def click(self):
            self._page.send_button_clicked = True

    async def patched_query(selector):
        if "Send" in selector or "send" in selector or "submit" in selector:
            return _RecordingSendBtn(browser.page)
        return browser.page.input_el

    browser.page.query_selector = patched_query
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: browser
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="Hi.",
        # draft_only omitted → defaults False
    ))

    # The send button WAS clicked (back-compat preserved)
    assert browser.page.send_button_clicked is True
    # Status is sent OR unknown depending on residual text — both are valid
    # "we actually clicked send" outcomes (not 'drafted' / 'blocked')
    assert result["status"] in ("sent", "unknown")


@pytest.mark.asyncio
async def test_draft_only_still_honors_url_guard(monkeypatch):
    """URL guard fires BEFORE draft_only branch — even drafting must
    not leak forbidden URLs into the compose box."""
    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("browser must not be touched")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="check my portfolio at github.com/foo",
        draft_only=True,
    ))

    assert result["status"] == "blocked"
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_draft_only_still_honors_product_pitch_guard(monkeypatch):
    """Product-pitch guard fires BEFORE draft_only branch too."""
    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("browser must not be touched")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message="LazyClaw runs the show",
        draft_only=True,
    ))

    assert result["status"] == "blocked"
    assert called["hit"] is False


def test_send_message_params_default_draft_only_false():
    """draft_only defaults to False — every existing caller that omits
    it keeps the send-and-verify behavior."""
    p = SendMessageParams(room_id="x", message="hi")
    assert p.draft_only is False


# ── _type_with_soft_breaks: the chunking-bug fix ────────────────────


class _RecordingKeyboard:
    """Records every keystroke + key press so tests can assert ORDER."""

    def __init__(self):
        self.events: list[tuple[str, str]] = []  # [(op, value), ...]

    async def type(self, text: str, *, delay: int = 0) -> None:
        self.events.append(("type", text))

    async def press(self, key: str) -> None:
        self.events.append(("press", key))


class _RecordingPage:
    def __init__(self):
        self.keyboard = _RecordingKeyboard()


@pytest.mark.asyncio
async def test_type_with_soft_breaks_single_line():
    """No newlines → one type() call, no Shift+Enter presses."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    await _type_with_soft_breaks(page, "Hello James")
    assert page.keyboard.events == [("type", "Hello James")]


@pytest.mark.asyncio
async def test_type_with_soft_breaks_uses_shift_enter_between_lines():
    """THE CHUNKING-BUG FIX: every `\\n` becomes `Shift+Enter`, NEVER `Enter`."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    await _type_with_soft_breaks(page, "Line 1\nLine 2\nLine 3")
    assert page.keyboard.events == [
        ("type", "Line 1"),
        ("press", "Shift+Enter"),
        ("type", "Line 2"),
        ("press", "Shift+Enter"),
        ("type", "Line 3"),
    ]
    # Absolute guarantee: not a single bare Enter
    for op, val in page.keyboard.events:
        if op == "press":
            assert val == "Shift+Enter", (
                f"Bare {val!r} press would trigger Upwork SEND — must use Shift+Enter"
            )


@pytest.mark.asyncio
async def test_type_with_soft_breaks_handles_blank_lines():
    """Markdown bullet lists often have blank lines between bullets.
    Each blank line should still become Shift+Enter (preserve formatting)."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    await _type_with_soft_breaks(page, "Hi\n\nQ1\n\nQ2")
    # Three lines with blanks between → 4 Shift+Enter presses
    presses = [v for op, v in page.keyboard.events if op == "press"]
    types = [v for op, v in page.keyboard.events if op == "type"]
    assert presses == ["Shift+Enter", "Shift+Enter", "Shift+Enter", "Shift+Enter"]
    assert types == ["Hi", "Q1", "Q2"]


@pytest.mark.asyncio
async def test_type_with_soft_breaks_normalizes_crlf():
    """Windows `\\r\\n` line endings must not double-break."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    await _type_with_soft_breaks(page, "Line 1\r\nLine 2\r\nLine 3")
    presses = [v for op, v in page.keyboard.events if op == "press"]
    # 2 line breaks for 3 lines, NOT 4 (would happen if \r\n → 2 \n)
    assert presses == ["Shift+Enter", "Shift+Enter"]


@pytest.mark.asyncio
async def test_type_with_soft_breaks_normalizes_lone_cr():
    """Old-Mac `\\r` line endings (and accidental CRs from clipboard
    paste) become a single Shift+Enter."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    await _type_with_soft_breaks(page, "Line 1\rLine 2")
    assert page.keyboard.events == [
        ("type", "Line 1"),
        ("press", "Shift+Enter"),
        ("type", "Line 2"),
    ]


@pytest.mark.asyncio
async def test_type_with_soft_breaks_reproduces_james_blue_draft():
    """End-to-end: the actual 11-line draft that caused the bug.
    Asserts: ZERO bare Enter presses (which would have caused the spam)."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    draft = (
        "Hey James, just checking in — ready to start whenever you are.\n"
        "\n"
        "Quick recap of what I need from you to kick off:\n"
        "\n"
        "1. Which platform am I monitoring?\n"
        "2. Property scraper — included or separate?\n"
        "3. Account access — PropStream/Reonomy/Crexi logins?\n"
        "4. First target cities/states?\n"
        "5. Alert delivery — push, SMS, or other?\n"
        "\n"
        "Answer in one reply and I can start the same day. Thanks."
    )
    await _type_with_soft_breaks(page, draft)
    presses = [v for op, v in page.keyboard.events if op == "press"]
    types = [v for op, v in page.keyboard.events if op == "type"]
    # Every press must be Shift+Enter — never a bare Enter
    assert all(p == "Shift+Enter" for p in presses), (
        f"Found a bare Enter press in {presses!r} — would have triggered Send mid-draft"
    )
    # The body lines (non-empty) all got typed
    assert "Hey James, just checking in — ready to start whenever you are." in types
    assert "1. Which platform am I monitoring?" in types
    assert "5. Alert delivery — push, SMS, or other?" in types
    assert "Answer in one reply and I can start the same day. Thanks." in types


@pytest.mark.asyncio
async def test_type_with_soft_breaks_empty_string():
    """Defensive — empty input is a no-op."""
    from upwork_mcp.tools.messages import _type_with_soft_breaks
    page = _RecordingPage()
    await _type_with_soft_breaks(page, "")
    # Single empty line → no type, no press
    assert page.keyboard.events == []


# ── edit_message: guards + param validation ────────────────────────


@pytest.mark.asyncio
async def test_edit_message_blocks_url_without_navigation(monkeypatch):
    """URL guard fires BEFORE any browser nav, same as send_message."""
    from upwork_mcp.tools.messages import EditMessageParams, edit_message

    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("browser must not be touched on URL block")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await edit_message(EditMessageParams(
        room_id="room_abc",
        message_index=0,
        new_content="check my portfolio at github.com/foo",
    ))
    assert result["status"] == "blocked"
    assert "github" in result["offending_token"].lower()
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_edit_message_blocks_product_pitch_without_navigation(monkeypatch):
    from upwork_mcp.tools.messages import EditMessageParams, edit_message

    called = {"hit": False}

    def _trip_wire(*_a, **_kw):
        called["hit"] = True
        raise RuntimeError("browser must not be touched on pitch block")

    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", _trip_wire
    )

    result = await edit_message(EditMessageParams(
        room_id="room_abc",
        message_index=0,
        new_content="LazyClaw runs everything",
    ))
    assert result["status"] == "blocked"
    assert called["hit"] is False


def test_edit_message_params_rejects_negative_index():
    """message_index must be >= 0 (the most recent of YOUR messages)."""
    from upwork_mcp.tools.messages import EditMessageParams
    with pytest.raises(Exception):  # pydantic ValidationError
        EditMessageParams(
            room_id="x", message_index=-1, new_content="y",
        )


def test_edit_message_params_default_draft_only_false():
    """draft_only defaults to False — explicit save behavior preserved."""
    from upwork_mcp.tools.messages import EditMessageParams
    p = EditMessageParams(
        room_id="x", message_index=0, new_content="y",
    )
    assert p.draft_only is False


def test_edit_message_params_accepts_draft_only_true():
    from upwork_mcp.tools.messages import EditMessageParams
    p = EditMessageParams(
        room_id="x", message_index=2, new_content="y\nz", draft_only=True,
    )
    assert p.draft_only is True
    assert p.message_index == 2


@pytest.mark.asyncio
async def test_send_message_uses_soft_breaks_helper(monkeypatch):
    """Integration: send_message routes contenteditable typing through
    _type_with_soft_breaks (the new helper), not through raw
    page.keyboard.type which would re-introduce the bug."""
    helper_called_with: dict = {}

    async def fake_helper(page, text):
        helper_called_with["text"] = text

    monkeypatch.setattr(
        "upwork_mcp.tools.messages._type_with_soft_breaks", fake_helper
    )

    # Reuse the existing _FakeBrowser scaffold from the draft_only tests
    browser = _FakeBrowser()
    monkeypatch.setattr(
        "upwork_mcp.tools.messages.get_browser", lambda: browser
    )

    # draft_only=True so we don't try to click Send (the fake raises if hit)
    msg = "Line A\nLine B\nLine C"
    result = await send_message(SendMessageParams(
        room_id="room_abc",
        message=msg,
        draft_only=True,
    ))

    assert result["status"] == "drafted"
    # The full message reached the helper (not page.keyboard.type directly)
    assert helper_called_with.get("text") == msg
