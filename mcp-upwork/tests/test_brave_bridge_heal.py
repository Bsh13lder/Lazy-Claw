"""Tests for the Brave-bridge self-heal hook in ``UpworkBrowser.start()``.

When the CDP port is unreachable from inside the container, ``start()``
must POST to the host launchd watcher's ``/heal`` endpoint and retry the
port check once before raising. Pure unit tests with mocks — no live
Chrome / no live watcher required.

Contract (negotiated with the sibling agent owning the host bridge):
    POST http://host.docker.internal:9224/heal
    200 OK  → bridge kicked; retry the port check
    other   → log + fall through to extended RuntimeError

Heal attempt is PER-CALL — two consecutive port-check failures inside a
single ``start()`` invocation must trigger AT MOST ONE heal POST.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


@pytest.mark.asyncio
async def test_start_calls_heal_endpoint_on_unreachable():
    """Port unreachable in container → /heal is POSTed exactly once.

    Even if the port is STILL unreachable after the heal (heal returned
    200 but Brave didn't come up), the heal call itself must have been
    made — and only one of it.
    """
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    post_mock = AsyncMock(return_value=MagicMock(status_code=200))
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None

    with patch(
        "upwork_mcp.browser.client.is_chrome_running_with_debug",
        return_value=False,
    ), patch(
        "upwork_mcp.browser.client._running_in_container",
        return_value=True,
    ), patch(
        "httpx.AsyncClient", return_value=client_cm,
    ), patch(
        "asyncio.sleep", new=AsyncMock(),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await b.start()

    # Heal called exactly once with the agreed contract URL.
    post_mock.assert_awaited_once_with("http://host.docker.internal:9224/heal")

    # Heal returned 200 but port still down → extended RuntimeError fires.
    msg = str(exc_info.value)
    assert "after self-heal attempt" in msg
    assert "install-host-brave-bridge.sh" in msg


@pytest.mark.asyncio
async def test_start_does_not_double_heal_in_one_call():
    """Two consecutive port-check failures inside one start() → ONE heal.

    Defends the PER-CALL semantics — if the retry loop ever grows past
    two iterations, the flag still blocks a second heal POST.
    """
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    post_mock = AsyncMock(return_value=MagicMock(status_code=200))
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None

    # is_chrome_running_with_debug is called MANY times (loop + heal
    # success branch). All return False so the retry path is exercised
    # to exhaustion.
    with patch(
        "upwork_mcp.browser.client.is_chrome_running_with_debug",
        return_value=False,
    ), patch(
        "upwork_mcp.browser.client._running_in_container",
        return_value=True,
    ), patch(
        "httpx.AsyncClient", return_value=client_cm,
    ), patch(
        "asyncio.sleep", new=AsyncMock(),
    ):
        with pytest.raises(RuntimeError):
            await b.start()

    # Exactly one heal POST regardless of how many port-check iterations
    # the loop did.
    assert post_mock.await_count == 1


@pytest.mark.asyncio
async def test_start_heal_endpoint_unreachable_falls_through():
    """httpx.ConnectError on /heal → original-style RuntimeError still raised.

    The extended message must explain that the host bridge is likely off,
    not just say "Cannot reach Chrome".
    """
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    post_mock = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None

    with patch(
        "upwork_mcp.browser.client.is_chrome_running_with_debug",
        return_value=False,
    ), patch(
        "upwork_mcp.browser.client._running_in_container",
        return_value=True,
    ), patch(
        "httpx.AsyncClient", return_value=client_cm,
    ), patch(
        "asyncio.sleep", new=AsyncMock(),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await b.start()

    post_mock.assert_awaited_once()

    msg = str(exc_info.value)
    assert "after self-heal attempt" in msg
    assert "install-host-brave-bridge.sh" in msg


@pytest.mark.asyncio
async def test_start_heal_endpoint_timeout_falls_through():
    """httpx.TimeoutException on /heal → extended RuntimeError still raised.

    Companion to the ConnectError case — the timeout branch of the heal
    block must produce the same extended diagnostic.
    """
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    post_mock = AsyncMock(side_effect=httpx.TimeoutException("read timeout"))
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None

    with patch(
        "upwork_mcp.browser.client.is_chrome_running_with_debug",
        return_value=False,
    ), patch(
        "upwork_mcp.browser.client._running_in_container",
        return_value=True,
    ), patch(
        "httpx.AsyncClient", return_value=client_cm,
    ), patch(
        "asyncio.sleep", new=AsyncMock(),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await b.start()

    post_mock.assert_awaited_once()
    msg = str(exc_info.value)
    assert "after self-heal attempt" in msg


@pytest.mark.asyncio
async def test_start_passes_through_when_port_listening():
    """Port is up from the start → no heal POST, no extended error.

    Happy path. The CDP connection itself is mocked so we don't need a
    real Brave; we just verify the heal hook stays dormant.
    """
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    fake_page = MagicMock(name="fake_page")
    fake_page.is_closed.return_value = False
    fake_page.set_default_timeout = MagicMock()

    fake_context = MagicMock()
    fake_context.pages = [fake_page]

    fake_browser = MagicMock()
    fake_browser.contexts = [fake_context]
    fake_browser.is_connected.return_value = True

    fake_playwright = MagicMock()
    fake_playwright.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)

    playwright_starter = MagicMock()
    playwright_starter.start = AsyncMock(return_value=fake_playwright)

    post_mock = AsyncMock(return_value=MagicMock(status_code=200))
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None

    with patch(
        "upwork_mcp.browser.client.is_chrome_running_with_debug",
        return_value=True,  # PORT LIVE
    ), patch(
        "upwork_mcp.browser.client._running_in_container",
        return_value=True,
    ), patch(
        "upwork_mcp.browser.client.async_playwright",
        return_value=playwright_starter,
    ), patch(
        "httpx.AsyncClient", return_value=client_cm,
    ):
        page = await b.start()

    # Happy path returned the page handle.
    assert page is fake_page
    # And critically: heal endpoint was NEVER called.
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_recovers_when_heal_kickstarts_brave():
    """Port comes up after a successful heal → no error, page returned.

    First port check fails → heal POST returns 200 → second port check
    sees the bridge alive → connection proceeds normally.
    """
    from upwork_mcp.browser.client import UpworkBrowser

    b = UpworkBrowser()

    # First call returns False (pre-heal), every call after returns True.
    call_count = {"n": 0}

    def fake_running():
        call_count["n"] += 1
        return call_count["n"] > 1

    fake_page = MagicMock(name="fake_page")
    fake_page.is_closed.return_value = False
    fake_page.set_default_timeout = MagicMock()

    fake_context = MagicMock()
    fake_context.pages = [fake_page]

    fake_browser = MagicMock()
    fake_browser.contexts = [fake_context]
    fake_browser.is_connected.return_value = True

    fake_playwright = MagicMock()
    fake_playwright.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)

    playwright_starter = MagicMock()
    playwright_starter.start = AsyncMock(return_value=fake_playwright)

    post_mock = AsyncMock(return_value=MagicMock(status_code=200))
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = MagicMock(post=post_mock)
    client_cm.__aexit__.return_value = None

    with patch(
        "upwork_mcp.browser.client.is_chrome_running_with_debug",
        side_effect=fake_running,
    ), patch(
        "upwork_mcp.browser.client._running_in_container",
        return_value=True,
    ), patch(
        "upwork_mcp.browser.client.async_playwright",
        return_value=playwright_starter,
    ), patch(
        "httpx.AsyncClient", return_value=client_cm,
    ), patch(
        "asyncio.sleep", new=AsyncMock(),
    ):
        page = await b.start()

    # Heal called exactly once.
    post_mock.assert_awaited_once_with("http://host.docker.internal:9224/heal")
    # Browser came up successfully.
    assert page is fake_page
