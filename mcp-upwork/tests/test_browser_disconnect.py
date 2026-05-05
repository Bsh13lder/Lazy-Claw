"""Tests for browser disconnect-recovery + truthful error messages.

Pure unit tests with mocks — do NOT require a running Chrome. Cover the
2026-05-04 root cause: stale Page handle between MCP tool calls produces
a 20ms "Not logged in" lie, and the brain fabricates a fake CLI command.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_is_disconnect_error_recognizes_playwright_strings():
    """The classifier matches every Playwright/Patchright disconnect message."""
    from upwork_mcp.browser.client import _is_disconnect_error

    for msg in [
        "Target closed",
        "Target page, context or browser has been closed",
        "Browser has been closed",
        "Connection closed while waiting for response",
        "Page closed unexpectedly",
        "BrowserContext closed",
        "Browser disconnected from the WebSocket",
    ]:
        assert _is_disconnect_error(RuntimeError(msg)) is True, msg

    # Real not-logged-in / unrelated errors must NOT classify as disconnect.
    for msg in [
        "Login form rejected credentials",
        "Element not found: button[type=submit]",
        "Timed out waiting for selector",
    ]:
        assert _is_disconnect_error(RuntimeError(msg)) is False, msg


@pytest.mark.asyncio
async def test_page_is_alive_false_when_not_started():
    """A fresh browser singleton reports its (absent) page as not alive."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()
    assert b._page_is_alive() is False


@pytest.mark.asyncio
async def test_page_is_alive_false_when_page_is_closed():
    """A page with is_closed()==True is not alive."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()
    b._started = True
    b._page = MagicMock()
    b._page.is_closed.return_value = True
    b._browser = MagicMock()
    b._browser.is_connected.return_value = True

    assert b._page_is_alive() is False


@pytest.mark.asyncio
async def test_page_is_alive_false_when_browser_disconnected():
    """A live page on a disconnected browser is not alive."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()
    b._started = True
    b._page = MagicMock()
    b._page.is_closed.return_value = False
    b._browser = MagicMock()
    b._browser.is_connected.return_value = False

    assert b._page_is_alive() is False


@pytest.mark.asyncio
async def test_page_is_alive_swallows_access_exceptions():
    """If is_closed() itself raises (handle proxy detached), report dead."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()
    b._started = True
    b._page = MagicMock()
    b._page.is_closed.side_effect = RuntimeError("Target closed")
    b._browser = MagicMock()

    assert b._page_is_alive() is False


@pytest.mark.asyncio
async def test_get_page_reconnects_when_stale():
    """get_page() must close+restart when the cached page is dead."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()
    b._started = True
    b._page = MagicMock()
    b._page.is_closed.return_value = True
    b._browser = MagicMock()
    b._browser.is_connected.return_value = True

    fresh = MagicMock(name="fresh_page")

    async def fake_start():
        b._started = True
        b._page = fresh
        return fresh

    with patch.object(b, "start", side_effect=fake_start) as mock_start, \
         patch.object(b, "close", new=AsyncMock()) as mock_close:
        page = await b.get_page()

    assert page is fresh
    mock_close.assert_awaited_once()
    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_is_logged_in_raises_typed_error_on_disconnect():
    """Disconnect during goto must raise BrowserDisconnectedError, not return False."""
    from upwork_mcp.browser.client import (
        UpworkBrowser, BrowserDisconnectedError,
    )

    b = UpworkBrowser()
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=RuntimeError("Target closed"))

    with patch.object(b, "get_page", new=AsyncMock(return_value=page)):
        with pytest.raises(BrowserDisconnectedError) as exc_info:
            await b.is_logged_in()

    assert "Target closed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ensure_logged_in_retries_once_on_disconnect():
    """ensure_logged_in() rebuilds + retries once on a disconnect."""
    from upwork_mcp.browser.client import (
        UpworkBrowser, BrowserDisconnectedError,
    )

    b = UpworkBrowser()

    calls = {"n": 0}

    async def flaky_check():
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserDisconnectedError("Target closed")
        return True

    with patch.object(b, "is_logged_in", side_effect=flaky_check), \
         patch.object(b, "close", new=AsyncMock()) as mock_close:
        result = await b.ensure_logged_in()

    assert result is True
    assert calls["n"] == 2
    mock_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_logged_in_surfaces_actual_error_after_retry():
    """Two disconnects in a row → surface a truthful runtime error."""
    from upwork_mcp.browser.client import (
        UpworkBrowser, BrowserDisconnectedError,
    )

    b = UpworkBrowser()

    async def always_disconnect():
        raise BrowserDisconnectedError("Connection closed twice")

    with patch.object(b, "is_logged_in", side_effect=always_disconnect), \
         patch.object(b, "close", new=AsyncMock()):
        with pytest.raises(RuntimeError) as exc_info:
            await b.ensure_logged_in()

    msg = str(exc_info.value)
    assert "bridge unreachable" in msg.lower() or "host" in msg.lower()
    assert "Connection closed twice" in msg
    # CRITICAL: must NOT advise the fake CLI command.
    assert "uvx upwork-mcp" not in msg


@pytest.mark.asyncio
async def test_ensure_logged_in_truthful_error_on_login_redirect():
    """Login redirect → error includes the actual current URL, no fake CLI advice."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    async def fake_check():
        b._last_state_url = "https://www.upwork.com/ab/account-security/login"
        b._last_state_reason = "login_redirect"
        return False

    with patch.object(b, "is_logged_in", side_effect=fake_check):
        with pytest.raises(RuntimeError) as exc_info:
            await b.ensure_logged_in()

    msg = str(exc_info.value)
    assert "ab/account-security/login" in msg
    assert "session expired" in msg.lower() or "signed out" in msg.lower()
    # CRITICAL: must NOT advise the fake CLI command.
    assert "uvx upwork-mcp" not in msg


@pytest.mark.asyncio
async def test_ensure_logged_in_truthful_error_on_cloudflare():
    """Cloudflare hold → distinct error mentions the challenge, no fake CLI."""
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    async def fake_check():
        b._last_state_url = "https://www.upwork.com/nx/find-work/best-matches"
        b._last_state_reason = "cloudflare_challenge"
        return False

    with patch.object(b, "is_logged_in", side_effect=fake_check):
        with pytest.raises(RuntimeError) as exc_info:
            await b.ensure_logged_in()

    msg = str(exc_info.value)
    assert "cloudflare" in msg.lower()
    assert "uvx upwork-mcp" not in msg
