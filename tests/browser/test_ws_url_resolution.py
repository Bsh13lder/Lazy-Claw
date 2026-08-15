"""Tests for explicit ws:// CDP URL resolution (Docker→host bridge).

Brave/Chrome report ``webSocketDebuggerUrl`` as ``ws://localhost:9222/...``
in ``/json/version``. From inside the container "localhost" resolves to the
container itself, so any client that trusts that URL verbatim silently
connects to the wrong host (or times out). ``rewrite_ws_host`` substitutes
the real bridge host/port while keeping the unique browser UUID path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.browser.cdp import resolve_browser_ws_url, rewrite_ws_host


class TestRewriteWsHost:
    def test_rewrites_localhost_to_docker_host(self):
        assert rewrite_ws_host(
            "ws://localhost:9222/devtools/browser/abc-123",
            host="host.docker.internal",
            port=9222,
        ) == "ws://host.docker.internal:9222/devtools/browser/abc-123"

    def test_rewrites_ip_host_and_port(self):
        assert rewrite_ws_host(
            "ws://127.0.0.1:9222/devtools/browser/uuid-x",
            host="192.168.65.2",
            port=9333,
        ) == "ws://192.168.65.2:9333/devtools/browser/uuid-x"

    def test_keeps_path_and_scheme_verbatim(self):
        out = rewrite_ws_host(
            "ws://localhost:9222/devtools/browser/6e2f-99a0",
            host="localhost",
            port=9222,
        )
        assert out == "ws://localhost:9222/devtools/browser/6e2f-99a0"

    def test_rejects_non_ws_url(self):
        with pytest.raises(ValueError):
            rewrite_ws_host(
                "http://localhost:9222/json/version",
                host="host.docker.internal",
                port=9222,
            )

    def test_rejects_empty_url(self):
        with pytest.raises(ValueError):
            rewrite_ws_host("", host="host.docker.internal", port=9222)


class TestResolveBrowserWsUrl:
    async def test_localhost_returns_rewritten_url(self):
        payload = {
            "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/uuid-1"
        }
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: payload
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            url = await resolve_browser_ws_url(port=9222, host="localhost")
        assert url == "ws://localhost:9222/devtools/browser/uuid-1"

    async def test_remote_host_is_dns_resolved_and_substituted(self):
        payload = {
            "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/uuid-2"
        }
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: payload
        with (
            patch("httpx.AsyncClient.get", return_value=mock_resp) as mock_get,
            patch("socket.gethostbyname", return_value="192.168.65.2"),
        ):
            url = await resolve_browser_ws_url(
                port=9222, host="host.docker.internal"
            )
        # The HTTP probe must target the resolved IP (Chromium validates the
        # Host header on the debug port — only IPs or literal localhost pass).
        assert "192.168.65.2" in str(mock_get.call_args)
        # And the returned ws URL must point at the resolved IP, never
        # the "localhost" the browser reported.
        assert url == "ws://192.168.65.2:9222/devtools/browser/uuid-2"

    async def test_unreachable_browser_returns_none(self):
        import httpx

        with patch(
            "httpx.AsyncClient.get", side_effect=httpx.ConnectError("boom")
        ):
            url = await resolve_browser_ws_url(port=9222, host="localhost")
        assert url is None

    async def test_dns_failure_returns_none(self):
        with patch("socket.gethostbyname", side_effect=OSError("no dns")):
            url = await resolve_browser_ws_url(
                port=9222, host="host.docker.internal"
            )
        assert url is None

    async def test_missing_ws_url_field_returns_none(self):
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {}
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            url = await resolve_browser_ws_url(port=9222, host="localhost")
        assert url is None
