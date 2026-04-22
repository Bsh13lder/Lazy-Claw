"""Pillar B — n8n-nodes MCP bundle + agent integration.

Guards the glue that connects czlonkowski/n8n-mcp (the node catalog)
to the agent so ANY model can look up node schemas instead of
memorizing them.

These are static checks — they do NOT launch the actual MCP process.
A full smoke test of `npx n8n-mcp` is handled in the plan's live
verification step.
"""

from __future__ import annotations

import pytest


# ── B1 — bundled MCP registration ──────────────────────────────────

def test_n8n_nodes_mcp_is_bundled():
    from lazyclaw.mcp.manager import BUNDLED_MCPS

    assert "n8n-nodes" in BUNDLED_MCPS, (
        "czlonkowski/n8n-mcp must be registered as a bundled MCP so "
        "the agent can auto-connect it when n8n keywords fire."
    )
    info = BUNDLED_MCPS["n8n-nodes"]
    assert info.get("npx") == "n8n-mcp", (
        "entry must point at the `n8n-mcp` npm package (czlonkowski's)."
    )
    # Default mode should be stdio + quiet logs so it works as a CLI
    # stdio MCP inside LazyClaw without an n8n instance.
    env = info.get("env") or {}
    assert env.get("MCP_MODE") == "stdio"


def test_n8n_existing_entry_is_unaffected():
    # Sanity — adding the node catalog must not break the existing
    # `n8n` bundled entry (leonardsellem's Python n8n-mcp-server).
    from lazyclaw.mcp.manager import BUNDLED_MCPS

    assert "n8n" in BUNDLED_MCPS
    assert BUNDLED_MCPS["n8n"].get("module") == "n8n_mcp"


# ── B3 — agent.py exposes the right suffixes ──────────────────────

def test_agent_declares_n8n_mcp_tool_suffixes():
    from lazyclaw.runtime import agent as agent_mod

    suffixes = getattr(agent_mod, "_N8N_MCP_TOOL_SUFFIXES", None)
    assert isinstance(suffixes, tuple), (
        "agent.py must declare _N8N_MCP_TOOL_SUFFIXES so the n8n "
        "keyword branch knows which MCP tools to inject."
    )
    # The minimum six that cover the progressive-disclosure flow
    # (search → describe → validate → exemplar).
    required = {
        "_search_nodes", "_get_node",
        "_validate_node", "_validate_workflow",
        "_search_templates", "_get_template",
    }
    missing = required - set(suffixes)
    assert not missing, f"missing suffixes: {missing}"


def test_agent_declares_n8n_mcp_server_name():
    from lazyclaw.runtime import agent as agent_mod

    assert getattr(agent_mod, "_N8N_MCP_SERVER_NAME", None) == "n8n-nodes", (
        "agent.py must match the BUNDLED_MCPS key so the on-demand "
        "connector can resolve the right server."
    )


def test_n8n_run_task_is_in_the_meta_bundle():
    # Regression — MiniMax narrated `<invoke name=\"n8n_run_task\">…`
    # as plain text because the tool wasn't in the injected bundle,
    # even though n8n_create_workflow's cheat sheet pointed at it.
    from lazyclaw.runtime import agent as agent_mod

    assert "n8n_run_task" in agent_mod._N8N_TOOL_NAMES


# ── B4 — second-pass validator is wired ────────────────────────────

def test_mcp_second_pass_validate_is_callable_and_safe_when_server_down():
    import asyncio

    from lazyclaw.skills.builtin import n8n_management as mod

    # With a bogus config, the helper must return [] (never raise),
    # so the native validator path stays in charge.
    async def _run():
        return await mod._mcp_second_pass_validate(
            config=None, user_id="nobody", workflow_json={"nodes": []},
        )

    result = asyncio.new_event_loop().run_until_complete(_run())
    assert result == []
