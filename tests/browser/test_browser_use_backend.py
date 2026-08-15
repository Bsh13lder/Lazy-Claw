"""Unit tests for the vendored browser-use backend (no live browser).

Pins the four contracts the 2026-08-15 adoption plan requires:

1. The vendored subset imports WITHOUT dragging in LLM SDKs / telemetry /
   bubus (dependency-capture guard).
2. Method-surface parity with CDPBackend — the drop-in contract that lets
   ``browser_settings["backend"]`` flip between them.
3. Actions publish to OUR event bus and route input through _SessionConn
   (which binds the CDP session id).
4. ``close()`` only detaches — it can never kill the user's signed-in Brave.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

from lazyclaw.browser.browser_use_backend import (
    BrowserUseBackend,
    _SessionConn,
    is_available,
)

_FORBIDDEN_MODULES = ("openai", "anthropic", "groq", "ollama", "posthog", "bubus")

# Methods the browser_actions handlers + backends.py actually call — the
# drop-in contract between CDPBackend and BrowserUseBackend.
_SHARED_SURFACE = [
    "goto", "current_url", "title", "content", "evaluate", "screenshot",
    "click", "type_text", "press_key", "scroll", "hover", "drag_and_drop",
    "tabs", "switch_tab", "new_tab", "close_tab",
    "find_element_by_role", "click_by_role", "wait_for_selector",
    "inject_console_capture", "get_console_logs",
    "is_connected", "close", "set_user_id", "set_cadence_overrides",
    "_ensure_connected",
]


class TestVendoredImportIsolation:
    def test_vendored_subset_available(self):
        assert is_available() is True

    def test_no_llm_sdk_telemetry_or_bus_leaks(self):
        from lazyclaw._vendor import ensure_vendor_path

        ensure_vendor_path()
        import browser_use.actor  # noqa: F401
        import browser_use.dom.service  # noqa: F401

        for banned in _FORBIDDEN_MODULES:
            loaded = [
                m for m in sys.modules
                if m == banned or m.startswith(banned + ".")
            ]
            assert not loaded, (
                f"vendored browser-use import leaked {banned!r}: {loaded}"
            )

    def test_vendored_copy_wins(self):
        from lazyclaw._vendor import ensure_vendor_path

        ensure_vendor_path()
        import browser_use

        assert "_vendor" in browser_use.__file__


class TestMethodSurfaceParity:
    def test_backend_exposes_shared_surface(self):
        from lazyclaw.browser.cdp_backend import CDPBackend

        for name in _SHARED_SURFACE:
            assert callable(getattr(BrowserUseBackend, name, None)), (
                f"BrowserUseBackend missing {name}()"
            )
            assert callable(getattr(CDPBackend, name, None)), (
                f"CDPBackend lost {name}() — parity contract broken"
            )

    def test_backend_type_and_profile_attr(self):
        backend = BrowserUseBackend(port=9222, profile_dir="/tmp/p", user_id="u1")
        assert backend.backend_type == "browser_use"
        # backends.py compares this attr to decide singleton reuse
        assert backend._profile_dir == "/tmp/p"


class TestSessionConn:
    async def test_send_binds_session_id(self):
        client = MagicMock()
        client.send_raw = AsyncMock(return_value={"ok": 1})
        conn = _SessionConn(client, "sess-1")

        out = await conn.send("Page.enable", {"a": 1})

        assert out == {"ok": 1}
        client.send_raw.assert_awaited_once_with(
            "Page.enable", {"a": 1}, session_id="sess-1"
        )

    async def test_send_defaults_empty_params(self):
        client = MagicMock()
        client.send_raw = AsyncMock(return_value={})
        conn = _SessionConn(client, None)

        await conn.send("Target.getTargets")

        client.send_raw.assert_awaited_once_with(
            "Target.getTargets", {}, session_id=None
        )


def _wired_backend() -> tuple[BrowserUseBackend, MagicMock]:
    """Backend with a fake CDP client wired in (no network)."""
    backend = BrowserUseBackend(port=9222, profile_dir="/tmp/p", user_id="u1")
    client = MagicMock()
    client.ws = object()  # "connected"
    client.send_raw = AsyncMock(return_value={"result": {"value": None}})
    client.stop = AsyncMock()
    backend._client = client
    backend._page = MagicMock()
    backend._page.press = AsyncMock()
    # actor's session_id is an async property — emulate with a coroutine attr
    type(backend._page).session_id = property(
        lambda self: _async_value("sess-9")
    )
    backend._target_id = "t1"
    return backend, client


async def _async_value(v):
    return v


class TestEventBusEmission:
    async def test_click_emits_action_event(self):
        backend, _client = _wired_backend()
        events = []
        with (
            patch(
                "lazyclaw.browser.event_bus.publish",
                side_effect=events.append,
            ),
            patch.object(
                backend, "evaluate",
                AsyncMock(return_value={"x": 10, "y": 20, "w": 30, "h": 15}),
            ),
            patch(
                "lazyclaw.browser.human_input.human_click", AsyncMock()
            ) as mock_click,
        ):
            await backend.click("#submit")

        assert mock_click.await_count == 1
        assert len(events) == 1
        evt = events[0]
        assert evt.user_id == "u1"
        assert evt.kind == "action"
        assert evt.action == "click"
        assert "#submit" in (evt.target or "")

    async def test_emit_is_noop_without_user_id(self):
        backend, _client = _wired_backend()
        backend.set_user_id(None)
        with patch("lazyclaw.browser.event_bus.publish") as mock_pub:
            backend._emit("action", action="click")
        mock_pub.assert_not_called()

    async def test_type_text_routes_through_human_input(self):
        backend, _client = _wired_backend()
        with (
            patch("lazyclaw.browser.event_bus.publish"),
            patch.object(
                backend, "evaluate",
                AsyncMock(return_value={"x": 1, "y": 2, "w": 10, "h": 10}),
            ),
            patch(
                "lazyclaw.browser.human_input.human_click", AsyncMock()
            ) as mock_click,
            patch(
                "lazyclaw.browser.human_input.human_type", AsyncMock()
            ) as mock_type,
        ):
            await backend.type_text("#field", "hello")

        assert mock_click.await_count == 1
        mock_type.assert_awaited_once()
        assert mock_type.await_args.args[1] == "hello"


class TestAttachOnlyLifecycle:
    async def test_close_detaches_but_never_kills_browser(self):
        backend, client = _wired_backend()

        await backend.close()

        client.stop.assert_awaited_once()
        # The one call that would kill the user's signed-in Brave.
        for call in client.send_raw.await_args_list:
            assert call.args[0] != "Browser.close", (
                "close() must never send Browser.close on an attached browser"
            )
        assert backend._client is None
        assert backend._page is None

    async def test_is_connected_reflects_ws_state(self):
        backend, client = _wired_backend()
        assert await backend.is_connected() is True
        client.ws = None
        assert await backend.is_connected() is False


class TestWsUrlResolution:
    async def test_host_source_rewrites_reported_localhost(self):
        backend = BrowserUseBackend(port=9222, user_id="u1")
        with (
            patch(
                "lazyclaw.browser.host_bridge.find_cdp_with_preference",
                AsyncMock(
                    return_value=(
                        "ws://localhost:9222/devtools/browser/uuid-7", "host"
                    )
                ),
            ),
            patch("socket.gethostbyname", return_value="192.168.65.2"),
        ):
            ws = await backend._resolve_ws_url()
        assert ws == "ws://192.168.65.2:9222/devtools/browser/uuid-7"
        assert backend._cdp_source == "host"

    async def test_local_source_kept_verbatim(self):
        backend = BrowserUseBackend(port=9222, user_id="u1")
        with patch(
            "lazyclaw.browser.host_bridge.find_cdp_with_preference",
            AsyncMock(
                return_value=(
                    "ws://localhost:9222/devtools/browser/uuid-8", "local"
                )
            ),
        ):
            ws = await backend._resolve_ws_url()
        assert ws == "ws://localhost:9222/devtools/browser/uuid-8"

    async def test_none_source_returns_none(self):
        backend = BrowserUseBackend(port=9222, user_id="u1")
        with patch(
            "lazyclaw.browser.host_bridge.find_cdp_with_preference",
            AsyncMock(return_value=(None, "none")),
        ):
            assert await backend._resolve_ws_url() is None


class TestBackendSelection:
    async def test_browser_use_pref_returns_vendored_backend(self):
        from lazyclaw.skills.builtin.browser_actions import backends

        backends.reset_backend()
        cfg = MagicMock(cdp_port=9222)
        with (
            patch.object(
                backends, "_get_user_backend_pref",
                AsyncMock(return_value="browser_use"),
            ),
            patch("lazyclaw.config.load_config", return_value=cfg),
            patch(
                "lazyclaw.browser.profile_resolver.resolve_profile_dir",
                return_value="/tmp/profiles/u1",
            ),
        ):
            backend = await backends.get_cdp_backend("u1")
        assert backend.backend_type == "browser_use"
        assert backend._user_id == "u1"
        backends.reset_backend()

    async def test_unavailable_vendor_falls_back_to_cdp(self):
        from lazyclaw.skills.builtin.browser_actions import backends

        backends.reset_backend()
        cfg = MagicMock(cdp_port=9222)
        with (
            patch.object(
                backends, "_get_user_backend_pref",
                AsyncMock(return_value="browser_use"),
            ),
            patch(
                "lazyclaw.browser.browser_use_backend.is_available",
                return_value=False,
            ),
            patch("lazyclaw.config.load_config", return_value=cfg),
            patch(
                "lazyclaw.browser.profile_resolver.resolve_profile_dir",
                return_value="/tmp/profiles/u1",
            ),
        ):
            backend = await backends.get_cdp_backend("u1")
        assert backend.backend_type != "browser_use"
        backends.reset_backend()

    async def test_default_pref_returns_cdp(self):
        from lazyclaw.skills.builtin.browser_actions import backends

        backends.reset_backend()
        cfg = MagicMock(cdp_port=9222)
        with (
            patch.object(
                backends, "_get_user_backend_pref",
                AsyncMock(return_value="cdp"),
            ),
            patch("lazyclaw.config.load_config", return_value=cfg),
            patch(
                "lazyclaw.browser.profile_resolver.resolve_profile_dir",
                return_value="/tmp/profiles/u1",
            ),
        ):
            backend = await backends.get_cdp_backend("u1")
        assert backend.backend_type != "browser_use"
        backends.reset_backend()
