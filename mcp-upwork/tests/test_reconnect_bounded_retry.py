"""Tests for the bounded CDP reconnect helper (stale-handle hardening).

Background (2026-06-10 audit): the CDP connection drops while Brave stays
alive; recovery used to be a ONE-SHOT retry inside ``safe_goto`` only.
Every other stale-handle path limped along on a dead singleton — 139
"handle stale" warnings in one day of stderr logs.

Contract under test (``UpworkBrowser.reconnect_with_retry``):
  * max 3 attempts, exponential backoff 1s / 2s between attempts;
  * full singleton reset (``close()``) before every attempt;
  * when attempt #2 ALSO fails with a connection-refused-style error,
    POST the Brave-bridge heal endpoint (env ``LAZYCLAW_BRAVE_HEAL_URL``,
    default ``http://127.0.0.1:9224/heal``, 5s timeout, failure
    tolerated) before the final attempt;
  * after exhausting retries, raise a RuntimeError whose message tells
    the calling agent something actionable.

Pure unit tests with mocks — no live Brave required. Mocking patterns
mirror tests/test_brave_bridge_heal.py.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


def _make_browser():
    from upwork_mcp.browser.client import UpworkBrowser

    return UpworkBrowser()


# ─── reconnect_with_retry: attempt/backoff semantics ─────────────────────


@pytest.mark.asyncio
async def test_reconnect_succeeds_first_attempt():
    """Healthy bridge → one close + one start, no backoff, no heal."""
    b = _make_browser()
    fake_page = MagicMock(name="fake_page")

    sleep_mock = AsyncMock()
    heal_mock = AsyncMock(return_value=True)
    with patch.object(b, "close", new=AsyncMock()) as close_mock, patch.object(
        b, "start", new=AsyncMock(return_value=fake_page)
    ) as start_mock, patch(
        "upwork_mcp.browser.client._post_brave_heal", new=heal_mock
    ), patch("asyncio.sleep", new=sleep_mock):
        page = await b.reconnect_with_retry()

    assert page is fake_page
    assert start_mock.await_count == 1
    assert close_mock.await_count == 1
    heal_mock.assert_not_awaited()
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_succeeds_second_attempt_with_1s_backoff():
    """Attempt 1 fails → sleep(1) → attempt 2 succeeds. No heal yet."""
    b = _make_browser()
    fake_page = MagicMock(name="fake_page")

    start_mock = AsyncMock(
        side_effect=[RuntimeError("Connection refused"), fake_page]
    )
    sleep_mock = AsyncMock()
    heal_mock = AsyncMock(return_value=True)
    with patch.object(b, "close", new=AsyncMock()), patch.object(
        b, "start", new=start_mock
    ), patch(
        "upwork_mcp.browser.client._post_brave_heal", new=heal_mock
    ), patch("asyncio.sleep", new=sleep_mock):
        page = await b.reconnect_with_retry()

    assert page is fake_page
    assert start_mock.await_count == 2
    # Heal only fires when attempt #2 ALSO fails.
    heal_mock.assert_not_awaited()
    sleep_mock.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_heal_called_after_second_refused_failure_then_third_succeeds():
    """Attempts 1+2 refused → heal POSTed once → attempt 3 succeeds.

    Backoff sequence must be 1s then 2s (exponential).
    """
    b = _make_browser()
    fake_page = MagicMock(name="fake_page")

    start_mock = AsyncMock(
        side_effect=[
            RuntimeError("connect ECONNREFUSED 127.0.0.1:9222"),
            RuntimeError("connect ECONNREFUSED 127.0.0.1:9222"),
            fake_page,
        ]
    )
    sleep_mock = AsyncMock()
    heal_mock = AsyncMock(return_value=True)
    with patch.object(b, "close", new=AsyncMock()), patch.object(
        b, "start", new=start_mock
    ), patch(
        "upwork_mcp.browser.client._post_brave_heal", new=heal_mock
    ), patch("asyncio.sleep", new=sleep_mock):
        page = await b.reconnect_with_retry()

    assert page is fake_page
    assert start_mock.await_count == 3
    heal_mock.assert_awaited_once()
    assert [c.args[0] for c in sleep_mock.await_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_heal_not_called_for_non_refused_errors():
    """Generic disconnect errors (not refused-style) never hit the heal
    endpoint — healing only helps when the bridge port itself is down."""
    b = _make_browser()

    start_mock = AsyncMock(side_effect=RuntimeError("Target closed"))
    heal_mock = AsyncMock(return_value=True)
    with patch.object(b, "close", new=AsyncMock()), patch.object(
        b, "start", new=start_mock
    ), patch(
        "upwork_mcp.browser.client._post_brave_heal", new=heal_mock
    ), patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await b.reconnect_with_retry()

    assert start_mock.await_count == 3
    heal_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_error_message_is_actionable():
    """All 3 attempts fail → RuntimeError tells the CALLER what to do."""
    b = _make_browser()

    start_mock = AsyncMock(side_effect=RuntimeError("Connection refused"))
    heal_mock = AsyncMock(return_value=False)
    with patch.object(b, "close", new=AsyncMock()), patch.object(
        b, "start", new=start_mock
    ), patch(
        "upwork_mcp.browser.client._post_brave_heal", new=heal_mock
    ), patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError) as exc_info:
            await b.reconnect_with_retry()

    assert start_mock.await_count == 3
    # Heal fired exactly once (after attempt #2's refused failure).
    heal_mock.assert_awaited_once()
    msg = str(exc_info.value)
    assert "could not heal after 3 attempts" in msg
    assert "Brave may need restart" in msg
    assert "fall back" in msg
    # Chain preserved for post-mortems.
    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_heal_failure_is_tolerated():
    """A raising heal hook must not mask the retry flow — attempt 3
    still runs and its success is returned."""
    b = _make_browser()
    fake_page = MagicMock(name="fake_page")

    start_mock = AsyncMock(
        side_effect=[
            RuntimeError("Connection refused"),
            RuntimeError("Connection refused"),
            fake_page,
        ]
    )
    # _post_brave_heal is contractually non-raising, but defend anyway.
    heal_mock = AsyncMock(side_effect=Exception("heal exploded"))
    with patch.object(b, "close", new=AsyncMock()), patch.object(
        b, "start", new=start_mock
    ), patch(
        "upwork_mcp.browser.client._post_brave_heal", new=heal_mock
    ), patch("asyncio.sleep", new=AsyncMock()):
        page = await b.reconnect_with_retry()

    assert page is fake_page
    assert start_mock.await_count == 3


# ─── _post_brave_heal: env URL + transport behavior ──────────────────────


def _httpx_client_mock(post_mock):
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None
    return client_cm


@pytest.mark.asyncio
async def test_post_brave_heal_uses_default_url(monkeypatch):
    from upwork_mcp.browser import client as client_mod

    monkeypatch.delenv("LAZYCLAW_BRAVE_HEAL_URL", raising=False)
    post_mock = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("httpx.AsyncClient", return_value=_httpx_client_mock(post_mock)):
        ok = await client_mod._post_brave_heal()

    assert ok is True
    post_mock.assert_awaited_once_with("http://127.0.0.1:9224/heal")


@pytest.mark.asyncio
async def test_post_brave_heal_honors_env_override(monkeypatch):
    from upwork_mcp.browser import client as client_mod

    monkeypatch.setenv(
        "LAZYCLAW_BRAVE_HEAL_URL", "http://host.docker.internal:9224/heal"
    )
    post_mock = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("httpx.AsyncClient", return_value=_httpx_client_mock(post_mock)):
        ok = await client_mod._post_brave_heal()

    assert ok is True
    post_mock.assert_awaited_once_with("http://host.docker.internal:9224/heal")


@pytest.mark.asyncio
async def test_post_brave_heal_never_raises(monkeypatch):
    from upwork_mcp.browser import client as client_mod

    monkeypatch.delenv("LAZYCLAW_BRAVE_HEAL_URL", raising=False)
    post_mock = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("httpx.AsyncClient", return_value=_httpx_client_mock(post_mock)):
        ok = await client_mod._post_brave_heal()

    assert ok is False


@pytest.mark.asyncio
async def test_post_brave_heal_non_200_returns_false(monkeypatch):
    from upwork_mcp.browser import client as client_mod

    monkeypatch.delenv("LAZYCLAW_BRAVE_HEAL_URL", raising=False)
    post_mock = AsyncMock(return_value=MagicMock(status_code=503))
    with patch("httpx.AsyncClient", return_value=_httpx_client_mock(post_mock)):
        ok = await client_mod._post_brave_heal()

    assert ok is False


# ─── wiring: stale-handle paths route through the bounded helper ─────────


@pytest.mark.asyncio
async def test_get_page_stale_handle_routes_to_reconnect():
    """A started browser with a dead handle must use the bounded helper,
    not the legacy one-shot close()+start()."""
    b = _make_browser()
    b._started = True
    b._page = MagicMock()
    b._page.is_closed.return_value = True  # dead handle
    b._browser = MagicMock()

    fake_page = MagicMock(name="fresh_page")
    reconnect_mock = AsyncMock(return_value=fake_page)
    with patch.object(b, "reconnect_with_retry", new=reconnect_mock):
        page = await b.get_page()

    assert page is fake_page
    reconnect_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_page_first_start_does_not_use_reconnect():
    """Never-started instance → plain start(); reconnect helper is for
    stale handles only (start() has its own container heal hook)."""
    b = _make_browser()

    fake_page = MagicMock(name="fresh_page")
    reconnect_mock = AsyncMock()
    with patch.object(b, "reconnect_with_retry", new=reconnect_mock), patch.object(
        b, "start", new=AsyncMock(return_value=fake_page)
    ) as start_mock:
        page = await b.get_page()

    assert page is fake_page
    start_mock.assert_awaited_once()
    reconnect_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_goto_disconnect_routes_to_reconnect():
    """goto() raising a disconnect error mid-call → safe_goto rebuilds
    the connection via the bounded helper and retries on the new page."""
    b = _make_browser()

    stale_page = MagicMock(name="stale_page")
    stale_page.url = "https://www.upwork.com/nx/find-work/"
    stale_page.goto = AsyncMock(
        side_effect=RuntimeError(
            "Page.goto: Target page, context or browser has been closed"
        )
    )

    fresh_page = MagicMock(name="fresh_page")
    fresh_page.url = "https://www.upwork.com/ab/messages/rooms/"
    fresh_page.goto = AsyncMock()
    fresh_page.content = AsyncMock(return_value="<html>inbox</html>")

    reconnect_mock = AsyncMock(return_value=fresh_page)
    with patch.object(
        b, "get_page", new=AsyncMock(return_value=stale_page)
    ), patch.object(b, "reconnect_with_retry", new=reconnect_mock), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        page = await b.safe_goto("https://www.upwork.com/ab/messages/rooms/")

    assert page is fresh_page
    reconnect_mock.assert_awaited_once()
    fresh_page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_logged_in_disconnect_routes_to_reconnect():
    """BrowserDisconnectedError during the login probe → bounded
    reconnect, then ONE retried login check."""
    from upwork_mcp.browser.client import BrowserDisconnectedError

    b = _make_browser()

    is_logged_in_mock = AsyncMock(
        side_effect=[BrowserDisconnectedError("Target closed"), True]
    )
    reconnect_mock = AsyncMock(return_value=MagicMock())
    with patch.object(b, "is_logged_in", new=is_logged_in_mock), patch.object(
        b, "reconnect_with_retry", new=reconnect_mock
    ):
        ok = await b.ensure_logged_in()

    assert ok is True
    reconnect_mock.assert_awaited_once()
    assert is_logged_in_mock.await_count == 2
