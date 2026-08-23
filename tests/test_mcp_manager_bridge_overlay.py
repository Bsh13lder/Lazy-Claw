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

import logging
import sys
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.gateway.internal_auth import get_internal_token
from lazyclaw.mcp import manager
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


def _stub_connect(
    monkeypatch, stored_config: dict, *, name: str = "mcp-apihunter",
) -> dict:
    """Point ``connect_server`` at a stored row and a recording fake client.

    No DB and no subprocess: ``get_server`` returns the row verbatim and the
    fake client captures the config ``connect_server`` hands it.
    """
    captured: dict = {}

    class _RecordingClient:
        def __init__(self, *, server_id, name, transport, config) -> None:
            captured["config"] = config

        async def connect(self) -> None:
            captured["connected"] = True

    async def _fake_get_server(config, user_id, server_id):
        return {
            "id": server_id,
            "name": name,
            "transport": "stdio",
            "config": stored_config,
            "enabled": True,
            "created_at": "2026-08-22T00:00:00",
            "connected": False,
        }

    monkeypatch.setattr(manager, "get_server", _fake_get_server)
    monkeypatch.setattr("lazyclaw.mcp.client.MCPClient", _RecordingClient)
    # Fresh registry so the test never touches (or leaks into) live clients.
    monkeypatch.setattr(manager, "_active_clients", {})
    return captured


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


def test_overlay_removes_stale_cdp_host_outside_docker(monkeypatch) -> None:
    """A row registered inside Docker must not keep pointing at the host bridge."""
    _stub_user_context(monkeypatch, docker=False)
    cfg = Config(port=18789)
    existing = {"LAZYCLAW_CDP_HOST": "host.docker.internal", "FOO": "bar"}
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-apihunter", existing_env=existing)
    assert "LAZYCLAW_CDP_HOST" not in env
    assert env["FOO"] == "bar"
    # The caller's dict is never touched.
    assert existing["LAZYCLAW_CDP_HOST"] == "host.docker.internal"


def test_overlay_removes_stale_cdp_port_when_env_unset(monkeypatch) -> None:
    """The parent process env is the only authority on the CDP port."""
    _stub_user_context(monkeypatch, docker=False)  # also clears LAZYCLAW_CDP_PORT
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(
        cfg, "u1", "mcp-upwork",
        existing_env={"LAZYCLAW_CDP_PORT": "9333", "FOO": "bar"},
    )
    assert "LAZYCLAW_CDP_PORT" not in env
    assert env["FOO"] == "bar"


def test_overlay_leaves_cdp_host_alone_when_docker_check_unavailable(monkeypatch) -> None:
    """No docker check = no knowledge, so the stored value must survive.

    ``docker=True`` here is the point: if the import had succeeded the key
    would have been rewritten to ``host.docker.internal``, so an untouched
    ``stale.example`` can only mean the ImportError branch ran.
    """
    _stub_user_context(monkeypatch, docker=True, profile_dir="/profiles/u1")
    # None in sys.modules makes ``from ... import ...`` raise ImportError.
    monkeypatch.setitem(sys.modules, "lazyclaw.browser.host_bridge", None)
    cfg = Config(port=18789)
    env = _build_bundled_env_overlay(
        cfg, "u1", "mcp-apihunter",
        existing_env={"LAZYCLAW_CDP_HOST": "stale.example"},
    )
    assert env["LAZYCLAW_CDP_HOST"] == "stale.example"
    # The rest of the user-context block still ran.
    assert env["LAZYCLAW_USER_ID"] == "u1"
    assert env["LAZYCLAW_BROWSER_PROFILE_DIR"] == "/profiles/u1"


def test_overlay_skips_user_context_for_non_optin_mcps(monkeypatch) -> None:
    _stub_user_context(monkeypatch, docker=True)
    cfg = Config(port=18789)
    # mcp-email doesn't opt in to inject_user_context — nothing is added, and
    # nothing is removed either.
    existing = {"FOO": "bar", "LAZYCLAW_CDP_HOST": "host.docker.internal"}
    env = _build_bundled_env_overlay(cfg, "u1", "mcp-email", existing_env=existing)
    assert env == existing


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


def test_canonical_config_heals_stale_but_existing_interpreter() -> None:
    """client.py only rescues a MISSING python path — an existing wrong one is ours."""
    stored = {"command": sys.base_prefix + "/bin/python3", "args": ["-m", "upwork_mcp"]}
    healed = _canonical_bundled_config("mcp-upwork", stored)
    assert healed["command"] == sys.executable
    assert healed["args"] == ["-m", "upwork_mcp"]


def test_canonical_config_skips_row_that_isnt_the_bundled_module() -> None:
    """A user-added MCP registered under a bundled name keeps its own command.

    Realistic collision: someone adds their own 'n8n' server. Only rows that
    already claim ``-m <bundled module>`` are ours to heal.
    """
    stored = {"command": "npx", "args": ["-y", "some-other-n8n-server"]}
    out = _canonical_bundled_config("n8n", stored)
    assert out == stored
    assert out is not stored


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


# -- connect_server wiring -----------------------------------------------------


async def test_connect_server_applies_heal_and_overlay(monkeypatch) -> None:
    """The contract the whole change exists for: what the subprocess receives."""
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/u1")
    captured = _stub_connect(
        monkeypatch,
        {"command": "/dead/python", "args": ["-m", "upwork_mcp"], "env": {}},
        name="mcp-upwork",
    )

    await manager.connect_server(Config(port=18789), "u1", "srv-1")

    assert captured["connected"] is True
    launched = captured["config"]
    assert launched["command"] == sys.executable
    assert launched["env"]["LAZYCLAW_USER_ID"] == "u1"
    assert launched["env"]["LAZYCLAW_BROWSER_PROFILE_DIR"] == "/profiles/u1"


async def test_connect_server_refuses_disabled_bundled_mcp(monkeypatch) -> None:
    """A disabled bundled MCP must never spawn — at ANY connect path."""
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/u1")
    captured = _stub_connect(
        monkeypatch,
        {"command": sys.executable, "args": ["-m", "mcp_apihunter"], "env": {}},
        name="mcp-apihunter",  # marked disabled in BUNDLED_MCPS
    )

    with pytest.raises(ValueError, match="disabled"):
        await manager.connect_server(Config(port=18789), "u1", "srv-1")

    # It never constructed or connected the client.
    assert "connected" not in captured


async def test_connect_server_logs_healed_keys_never_values(monkeypatch, caplog) -> None:
    """The DB/UI still show the stale row — the log is the only breadcrumb.

    Env values carry user ids and filesystem paths, so only key names may be
    logged.
    """
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/private-user-dir")
    _stub_connect(
        monkeypatch,
        {"command": "/dead/python", "args": ["-m", "upwork_mcp"], "env": {}},
        name="mcp-upwork",
    )

    with caplog.at_level(logging.INFO, logger="lazyclaw.mcp.manager"):
        await manager.connect_server(Config(port=18789), "u1", "srv-1")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "command" in logged
    assert "LAZYCLAW_USER_ID" in logged
    assert "LAZYCLAW_BROWSER_PROFILE_DIR" in logged
    assert "/profiles/private-user-dir" not in logged
    assert sys.executable not in logged


async def test_connect_server_stays_quiet_when_nothing_drifted(monkeypatch, caplog) -> None:
    _stub_user_context(monkeypatch, docker=False, profile_dir="/profiles/u1")
    fresh_env = {
        "LAZYCLAW_USER_ID": "u1",
        "LAZYCLAW_BROWSER_PROFILE_DIR": "/profiles/u1",
    }
    _stub_connect(
        monkeypatch,
        {"command": sys.executable, "args": ["-m", "upwork_mcp"], "env": fresh_env},
        name="mcp-upwork",
    )

    with caplog.at_level(logging.INFO, logger="lazyclaw.mcp.manager"):
        await manager.connect_server(Config(port=18789), "u1", "srv-1")

    # Only the usual "Connected to MCP server" line — no drift line, so a
    # healthy row can't spam the log on every reconnect.
    ours = [r for r in caplog.records if r.name == "lazyclaw.mcp.manager"]
    assert len(ours) == 1
    assert "Connected to MCP server" in ours[0].getMessage()
