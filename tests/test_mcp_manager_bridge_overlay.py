"""Tests for the per-connect env overlay that wires the contact-store bridge.

The overlay is what makes mcp-whatsapp see ``LAZYCLAW_INTERNAL_TOKEN`` /
``LAZYCLAW_GATEWAY_URL`` / ``LAZYCLAW_USER_ID`` at runtime. We can't (and
shouldn't) store the token in the encrypted DB because it rotates per
process; this test pins the contract that the overlay does the rotation.
"""

from __future__ import annotations

from lazyclaw.config import Config
from lazyclaw.gateway.internal_auth import get_internal_token
from lazyclaw.mcp.manager import _build_bundled_env_overlay


def test_overlay_injects_bridge_env_for_whatsapp() -> None:
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-whatsapp", existing_env={})
    assert env["LAZYCLAW_USER_ID"] == "u1"
    assert env["LAZYCLAW_GATEWAY_URL"] == "http://127.0.0.1:18789"
    assert env["LAZYCLAW_INTERNAL_TOKEN"] == get_internal_token()


def test_overlay_respects_explicit_gateway_url(monkeypatch) -> None:
    monkeypatch.setenv("LAZYCLAW_GATEWAY_URL", "http://gateway.local:9999")
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-whatsapp", existing_env={})
    assert env["LAZYCLAW_GATEWAY_URL"] == "http://gateway.local:9999"


def test_overlay_skips_bridge_for_non_optin_mcps() -> None:
    cfg = Config(port=18789)
    # mcp-email doesn't opt in to inject_contact_bridge.
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-email", existing_env={})
    assert "LAZYCLAW_INTERNAL_TOKEN" not in env
    assert "LAZYCLAW_USER_ID" not in env


def test_overlay_preserves_existing_env_keys() -> None:
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(
        cfg, "u1", "mcp-whatsapp",
        existing_env={"WHATSAPP_DATA_DIR": "/tmp/x", "FOO": "bar"},
    )
    assert env["WHATSAPP_DATA_DIR"] == "/tmp/x"
    assert env["FOO"] == "bar"
    # Bridge keys still added on top.
    assert env["LAZYCLAW_USER_ID"] == "u1"


def test_overlay_unknown_mcp_returns_copy() -> None:
    cfg = Config()
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-does-not-exist", existing_env={"K": "v"})
    assert env == {"K": "v"}
