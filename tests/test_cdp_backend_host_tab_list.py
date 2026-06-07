"""Host-bridge tab enumeration must target the host gateway, not localhost.

Regression (2026-06-06): the Upwork watcher opened a NEW visible tab on the
user's Brave on EVERY poll and never settled. Root cause: in host-bridge mode
(``_cdp_source == "host"``) the user's real tabs live on
``host.docker.internal`` — but ``CDPBackend.tabs()`` and ``switch_tab()`` called
``list_chrome_tabs(self._port)`` with the default ``host="localhost"``. Inside
the Docker container ``localhost:9222/json`` is the *container*, not the host
Brave, so the call returned ``[]``:

  * ``tabs()`` → ``[]`` → the watcher's ``anchor_target_id`` never matched →
    it fell through to "create a new parked tab" on every poll.
  * ``new_tab()`` created a (visible) tab on the host → "jumping on screen".
  * ``switch_tab()`` → ``[]`` → ``ValueError: Tab not found`` → the watcher
    swallowed it and saved context WITHOUT the anchor → infinite re-create.

The connect path already used ``host=HOST_GATEWAY_HOSTNAME`` (cdp_backend.py
:514); these two tab ops simply forgot to.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lazyclaw.browser import cdp_backend
from lazyclaw.browser.cdp_backend import CDPBackend
from lazyclaw.browser.host_bridge import HOST_GATEWAY_HOSTNAME


def _make_list_recorder(return_tabs):
    seen: dict = {}

    async def _fake_list(port, host="localhost"):
        seen["port"] = port
        seen["host"] = host
        return list(return_tabs)

    return _fake_list, seen


@pytest.mark.asyncio
async def test_tabs_queries_host_gateway_when_source_host(monkeypatch):
    tab = SimpleNamespace(
        id="HOSTTAB",
        title="Messages",
        url="https://www.upwork.com/ab/messages/rooms/",
        ws_url="ws://host/devtools/page/HOSTTAB",
        tab_type="page",
    )
    fake_list, seen = _make_list_recorder([tab])
    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", fake_list)

    b = CDPBackend(port=9222, user_id="u1")
    b._cdp_source = "host"

    tabs = await b.tabs()

    assert seen["host"] == HOST_GATEWAY_HOSTNAME
    assert [t.id for t in tabs] == ["HOSTTAB"]


@pytest.mark.asyncio
async def test_tabs_queries_localhost_when_source_local(monkeypatch):
    fake_list, seen = _make_list_recorder([])
    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", fake_list)

    b = CDPBackend(port=9222, user_id="u1")
    b._cdp_source = "local"

    await b.tabs()

    assert seen["host"] == "localhost"


@pytest.mark.asyncio
async def test_tabs_queries_localhost_when_source_unknown(monkeypatch):
    """Before the first connect (``_cdp_source is None``) we must not assume
    host mode — default to localhost exactly as before."""
    fake_list, seen = _make_list_recorder([])
    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", fake_list)

    b = CDPBackend(port=9222, user_id="u1")
    # _cdp_source defaults to None

    await b.tabs()

    assert seen["host"] == "localhost"


@pytest.mark.asyncio
async def test_switch_tab_looks_up_host_gateway_when_source_host(monkeypatch):
    # Empty list → switch_tab raises "Tab not found", but we only care that it
    # looked on the HOST gateway (so a real anchored tab WOULD be found there).
    fake_list, seen = _make_list_recorder([])
    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", fake_list)

    b = CDPBackend(port=9222, user_id="u1")
    b._cdp_source = "host"

    with pytest.raises(ValueError, match="Tab not found"):
        await b.switch_tab("ANCHOR1", focus=False)

    assert seen["host"] == HOST_GATEWAY_HOSTNAME


@pytest.mark.asyncio
async def test_switch_tab_looks_up_localhost_when_source_local(monkeypatch):
    fake_list, seen = _make_list_recorder([])
    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", fake_list)

    b = CDPBackend(port=9222, user_id="u1")
    b._cdp_source = "local"

    with pytest.raises(ValueError, match="Tab not found"):
        await b.switch_tab("ANCHOR1", focus=False)

    assert seen["host"] == "localhost"
