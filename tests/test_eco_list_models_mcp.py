"""Tests for the MCP-provided-models section in EcoListModelsSkill.

Covers the pure `_render_mcp_models_section` helper so we can verify the
output shape without spinning up DB / config / async machinery.
"""
from __future__ import annotations

import pytest

from lazyclaw.skills.builtin.eco_management import _render_mcp_models_section


# ── Fakes ────────────────────────────────────────────────────────────


def _server(name: str, *, connected: bool = True) -> dict:
    return {
        "id": f"id-of-{name}",
        "name": name,
        "connected": connected,
        "status": "connected" if connected else "disconnected",
    }


_CLAUDE_CODE_MANIFEST = {
    "claude-code": {
        "description": "claude-code MCP",
        "models": [
            {
                "id": "claude-opus-4-8",
                "display_name": "Claude Opus 4.8 (via Claude Code MCP)",
                "context_tokens": 1_000_000,
                "cost": "$0 via Claude Code subscription",
            },
        ],
    },
}

_TOOL_ONLY_MANIFEST = {
    "mcp-upwork": {
        "description": "upwork tools, no models",
        # NO models field
    },
}

_MIXED_MANIFEST = {**_CLAUDE_CODE_MANIFEST, **_TOOL_ONLY_MANIFEST}


# ── Connected model-backed MCP appears in output ─────────────────────


def test_connected_claude_code_renders_with_model_details() -> None:
    servers = [_server("claude-code", connected=True)]
    lines = _render_mcp_models_section(servers, _CLAUDE_CODE_MANIFEST)
    text = "\n".join(lines)
    assert "MCP-PROVIDED MODELS:" in text
    assert "claude-code" in text
    assert "✓ connected" in text
    assert "Claude Opus 4.8" in text
    assert "Context: 1M tokens" in text
    assert "$0 via Claude Code subscription" in text


def test_disconnected_model_backed_mcp_still_listed_with_marker() -> None:
    servers = [_server("claude-code", connected=False)]
    lines = _render_mcp_models_section(servers, _CLAUDE_CODE_MANIFEST)
    text = "\n".join(lines)
    assert "✗ not connected" in text
    assert "Claude Opus 4.8" in text


# ── Tool-only MCPs do NOT pollute the model list ─────────────────────


def test_tool_only_mcp_absent_from_section() -> None:
    servers = [_server("mcp-upwork", connected=True)]
    lines = _render_mcp_models_section(servers, _TOOL_ONLY_MANIFEST)
    # No section at all when nobody advertises a model
    assert lines == []


def test_mixed_servers_only_model_backed_render() -> None:
    servers = [
        _server("mcp-upwork", connected=True),
        _server("claude-code", connected=True),
    ]
    lines = _render_mcp_models_section(servers, _MIXED_MANIFEST)
    text = "\n".join(lines)
    assert "claude-code" in text
    assert "mcp-upwork" not in text


# ── Defensive edge cases ─────────────────────────────────────────────


def test_empty_servers_returns_empty_lines() -> None:
    assert _render_mcp_models_section([], _MIXED_MANIFEST) == []


def test_server_not_in_manifest_skipped() -> None:
    # User has manually installed an MCP we don't have a manifest entry
    # for — must not crash, must not appear in the model list.
    servers = [_server("home-grown-mcp", connected=True)]
    assert _render_mcp_models_section(servers, _MIXED_MANIFEST) == []


def test_manifest_with_empty_models_list_skipped() -> None:
    servers = [_server("claude-code", connected=True)]
    manifest = {"claude-code": {"description": "x", "models": []}}
    assert _render_mcp_models_section(servers, manifest) == []


def test_model_without_display_name_falls_back_to_id() -> None:
    servers = [_server("custom-mcp", connected=True)]
    manifest = {
        "custom-mcp": {
            "description": "x",
            "models": [{"id": "raw-id-only", "context_tokens": 128_000}],
        },
    }
    lines = _render_mcp_models_section(servers, manifest)
    text = "\n".join(lines)
    assert "raw-id-only" in text
    assert "Context: 128K tokens" in text


def test_context_below_1k_renders_raw_number() -> None:
    servers = [_server("custom-mcp", connected=True)]
    manifest = {
        "custom-mcp": {
            "description": "x",
            "models": [{"id": "tiny", "context_tokens": 512}],
        },
    }
    lines = _render_mcp_models_section(servers, manifest)
    text = "\n".join(lines)
    assert "Context: 512 tokens" in text


def test_model_without_context_or_cost_renders_just_name() -> None:
    servers = [_server("custom-mcp", connected=True)]
    manifest = {
        "custom-mcp": {
            "description": "x",
            "models": [{"id": "bare-id"}],
        },
    }
    lines = _render_mcp_models_section(servers, manifest)
    text = "\n".join(lines)
    # Model name present, but no Context: or Cost: lines
    assert "bare-id" in text
    assert "Context:" not in text
    assert "Cost:" not in text


def test_section_header_only_emitted_once_for_multiple_mcps() -> None:
    servers = [
        _server("claude-code", connected=True),
        _server("custom-mcp", connected=True),
    ]
    manifest = {
        **_CLAUDE_CODE_MANIFEST,
        "custom-mcp": {
            "description": "x",
            "models": [{"id": "another-model"}],
        },
    }
    lines = _render_mcp_models_section(servers, manifest)
    text = "\n".join(lines)
    # Header should appear exactly once
    assert text.count("MCP-PROVIDED MODELS:") == 1
    # Both MCPs render
    assert "claude-code" in text
    assert "custom-mcp" in text


# ── BUNDLED_MCPS live wiring sanity ──────────────────────────────────


def test_live_claude_code_entry_has_models_field() -> None:
    # Guards against someone removing the models field while refactoring
    # BUNDLED_MCPS. If this fails, eco_list_models stops listing Opus.
    from lazyclaw.mcp.manager import BUNDLED_MCPS
    entry = BUNDLED_MCPS.get("claude-code") or {}
    assert "models" in entry
    assert entry["models"]
    first = entry["models"][0]
    assert first.get("id")
    assert first.get("display_name")
