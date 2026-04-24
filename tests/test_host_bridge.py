"""Tests for the host-browser CDP bridge helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from lazyclaw.browser import host_bridge


def test_generate_host_token_shape():
    t = host_bridge.generate_host_token()
    assert isinstance(t, str)
    # token_urlsafe(16) yields ~22 chars; minimum sanity bound
    assert len(t) >= 16


def test_origin_for_token_format():
    t = "abc123"
    assert host_bridge.origin_for_token(t) == "http://lazyclaw-abc123"


def test_build_launch_command_contains_critical_flags():
    token = "TOK"
    cmd = host_bridge.build_launch_command(token)
    assert "--remote-debugging-port=9222" in cmd
    assert "--remote-debugging-address=0.0.0.0" in cmd
    assert "--remote-allow-origins=http://lazyclaw-TOK" in cmd
    assert "Cmd+Q" in cmd  # quit-first reminder
    assert "Brave Browser" in cmd  # default app


def test_build_launch_command_chrome_variant():
    cmd = host_bridge.build_launch_command("TOK", browser="chrome")
    assert "Google Chrome" in cmd
    assert "Google/Chrome" in cmd  # user-data-dir path


def test_is_docker_runtime_env_flag(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_SERVER_MODE", "true")
    assert host_bridge.is_docker_runtime() is True
    monkeypatch.setenv("LAZYCLAW_SERVER_MODE", "false")
    # On macOS without /.dockerenv this should be False
    import sys
    if sys.platform == "darwin":
        assert host_bridge.is_docker_runtime() is False


def test_security_warning_is_nonempty_string():
    w = host_bridge.security_warning()
    assert isinstance(w, str)
    assert len(w) > 20


def test_probe_host_cdp_returns_none_when_unreachable():
    """Real network call to a bogus host — httpx should surface a connect error
    which we catch and report as None. No monkeypatching needed; the call
    should be fast enough for the 2s timeout."""
    async def run():
        # host.docker.internal may resolve or not depending on environment;
        # but 127.0.0.1:1 is guaranteed refused.
        with patch.object(host_bridge, "HOST_GATEWAY_HOSTNAME", "127.0.0.1"):
            return await host_bridge.probe_host_cdp(port=1, timeout_s=0.5)

    result = asyncio.run(run())
    assert result is None


def test_find_cdp_with_preference_falls_through_to_local():
    """When prefer_host is False, only localhost is probed."""
    async def run():
        with patch.object(host_bridge, "probe_host_cdp") as host_probe, \
             patch("lazyclaw.browser.cdp.find_chrome_cdp") as local_probe:
            host_probe.return_value = None
            local_probe.return_value = "ws://localhost:9222/x"

            ws, source = await host_bridge.find_cdp_with_preference(
                port=9222, prefer_host=False, token=None,
            )
            assert ws == "ws://localhost:9222/x"
            assert source == "local"
            # When prefer_host=False the host probe should NOT run
            host_probe.assert_not_called()

    asyncio.run(run())


def test_find_cdp_with_preference_prefers_host_when_available(monkeypatch):
    """When docker runtime + prefer_host=True + host probe succeeds, we use host."""
    monkeypatch.setenv("LAZYCLAW_SERVER_MODE", "true")

    async def run():
        with patch.object(host_bridge, "probe_host_cdp") as host_probe, \
             patch("lazyclaw.browser.cdp.find_chrome_cdp") as local_probe:
            host_probe.return_value = "ws://host.docker.internal:9222/x"
            local_probe.return_value = "ws://localhost:9222/y"

            ws, source = await host_bridge.find_cdp_with_preference(
                port=9222, prefer_host=True, token="tok",
            )
            assert ws == "ws://host.docker.internal:9222/x"
            assert source == "host"
            # Local probe should NOT run when host succeeds
            local_probe.assert_not_called()

    asyncio.run(run())


def test_find_cdp_with_preference_host_miss_then_local_hit(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_SERVER_MODE", "true")

    async def run():
        with patch.object(host_bridge, "probe_host_cdp") as host_probe, \
             patch("lazyclaw.browser.cdp.find_chrome_cdp") as local_probe:
            host_probe.return_value = None
            local_probe.return_value = "ws://localhost:9222/x"

            ws, source = await host_bridge.find_cdp_with_preference(
                port=9222, prefer_host=True, token="tok",
            )
            assert source == "local"
            assert ws == "ws://localhost:9222/x"

    asyncio.run(run())


def test_find_cdp_with_preference_nothing_reachable(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_SERVER_MODE", "true")

    async def run():
        with patch.object(host_bridge, "probe_host_cdp") as host_probe, \
             patch("lazyclaw.browser.cdp.find_chrome_cdp") as local_probe:
            host_probe.return_value = None
            local_probe.return_value = None

            ws, source = await host_bridge.find_cdp_with_preference(
                port=9222, prefer_host=True, token="tok",
            )
            assert ws is None
            assert source == "none"

    asyncio.run(run())
