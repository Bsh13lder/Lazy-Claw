"""Tests for the per-connect refresh of a bundled MCP's env and command.

The overlay is what makes mcp-whatsapp see ``LAZYCLAW_INTERNAL_TOKEN`` /
``LAZYCLAW_GATEWAY_URL`` / ``LAZYCLAW_USER_ID`` at runtime. We can't (and
shouldn't) store the token in the encrypted DB because it rotates per
process; this test pins the contract that the overlay does the rotation.

The same connect-time refresh covers the ``inject_user_context`` MCPs
(mcp-apihunter, mcp-upwork) and the launch command of module-based bundled
MCPs — both are derivable, and stored rows drift because registration is
skip-if-exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lazyclaw.config import Config
from lazyclaw.gateway.internal_auth import get_internal_token
from lazyclaw.mcp.manager import _build_bundled_env_overlay, _canonical_bundled_config


def _stub_user_context(
    monkeypatch, *, docker: bool, profile_dir: str = "/profiles/u1",
) -> None:
    """Pin the two host-dependent lookups the user-context overlay makes.

    Keeps the suite independent of whether it runs inside Docker and of
    whether the user's browser profile exists on disk.
    """
    monkeypatch.setattr(
        "lazyclaw.browser.profile_resolver.resolve_profile_dir",
        lambda config, user_id, account_slug=None: Path(profile_dir),
    )
    monkeypatch.setattr(
        "lazyclaw.browser.host_bridge.is_docker_runtime",
        lambda: docker,
    )
    monkeypatch.delenv("LAZYCLAW_CDP_PORT", raising=False)


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


# -- inject_user_context (mcp-apihunter, mcp-upwork) ---------------------------


def test_overlay_injects_user_context_for_apihunter(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/u1")
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-apihunter", existing_env={})
    assert env["LAZYCLAW_USER_ID"] == "u1"
    assert env["LAZYCLAW_BROWSER_PROFILE_DIR"] == "/profiles/u1"


def test_overlay_injects_user_context_for_upwork(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/u2")
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u2", "mcp-upwork", existing_env={})
    assert env["LAZYCLAW_USER_ID"] == "u2"
    assert env["LAZYCLAW_BROWSER_PROFILE_DIR"] == "/profiles/u2"


def test_overlay_overwrites_stale_user_context_values(monkeypatch) -> None:
    """A row registered by an older install must not win over fresh values."""
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/fresh")
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(
        cfg, "u1", "mcp-apihunter",
        existing_env={
            "LAZYCLAW_USER_ID": "some-other-user",
            "LAZYCLAW_BROWSER_PROFILE_DIR": "/Users/host/browser_profiles/old",
            "APIHUNTER_DATA_DIR": "/data/apihunter",
        },
    )
    assert env["LAZYCLAW_USER_ID"] == "u1"
    assert env["LAZYCLAW_BROWSER_PROFILE_DIR"] == "/profiles/fresh"
    # Keys the overlay doesn't own survive untouched.
    assert env["APIHUNTER_DATA_DIR"] == "/data/apihunter"


def test_overlay_includes_cdp_port_when_env_set(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=False)
    monkeypatch.setenv("LAZYCLAW_CDP_PORT", "9222")
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-apihunter", existing_env={})
    assert env["LAZYCLAW_CDP_PORT"] == "9222"


def test_overlay_omits_cdp_port_when_env_unset(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=False)
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-apihunter", existing_env={})
    assert "LAZYCLAW_CDP_PORT" not in env


def test_overlay_sets_cdp_host_inside_docker(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=True)
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-upwork", existing_env={})
    assert env["LAZYCLAW_CDP_HOST"] == "host.docker.internal"


def test_overlay_omits_cdp_host_outside_docker(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=False)
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-upwork", existing_env={})
    assert "LAZYCLAW_CDP_HOST" not in env


def test_overlay_skips_user_context_for_non_optin_mcps(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=True)
    cfg = Config(port=18789)
    # mcp-email doesn't opt in to inject_user_context.
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-email", existing_env={"FOO": "bar"})
    assert env == {"FOO": "bar"}


# -- command healing -----------------------------------------------------------


def test_canonical_config_heals_dead_interpreter_for_module_mcp() -> None:
    stored = {
        "command": "/Users/host/lazyclaw/.venv/bin/python",
        "args": ["-m", "mcp_apihunter"],
        "env": {"LAZYCLAW_USER_ID": "u1"},
    }
    healed = _canonical_bundled_config("mcp-apihunter", stored)
    assert healed["command"] == sys.executable
    assert healed["args"] == ["-m", "mcp_apihunter"]
    # Keys the helper doesn't own pass through.
    assert healed["env"] == {"LAZYCLAW_USER_ID": "u1"}


def test_canonical_config_rebuilds_module_args() -> None:
    stored = {"command": "/dead/python", "args": ["-m", "upwork_mcp_legacy", "--flag"]}
    healed = _canonical_bundled_config("mcp-upwork", stored)
    assert healed["args"] == ["-m", "upwork_mcp"]


def test_canonical_config_does_not_mutate_input() -> None:
    stored = {"command": "/dead/python", "args": ["-m", "upwork_mcp"]}
    _canonical_bundled_config("mcp-upwork", stored)
    assert stored == {"command": "/dead/python", "args": ["-m", "upwork_mcp"]}


def test_canonical_config_leaves_npx_entry_unchanged() -> None:
    stored = {"command": "npx", "args": ["-y", "@steipete/claude-code-mcp"]}
    out = _canonical_bundled_config("claude-code", stored)
    assert out == stored
    assert out is not stored


def test_canonical_config_leaves_bin_entry_unchanged() -> None:
    stored = {"command": "/usr/local/bin/workspace-mcp", "args": ["--single-user"]}
    out = _canonical_bundled_config("workspace-mcp", stored)
    assert out == stored


def test_canonical_config_leaves_unknown_name_unchanged() -> None:
    stored = {"command": "/usr/bin/custom-mcp", "args": ["--serve"]}
    out = _canonical_bundled_config("some-user-added-mcp", stored)
    assert out == stored
    assert out is not stored
