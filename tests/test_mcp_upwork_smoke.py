"""Smoke test for mcp-upwork — list_tools over stdio.

No network, no Brave login required. Just verifies the MCP server starts,
honors LAZYCLAW_BROWSER_PROFILE_DIR + LAZYCLAW_CDP_PORT env injection,
and advertises all upstream tools.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest


async def _list_tools_with_env_injection():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["LAZYCLAW_BROWSER_PROFILE_DIR"] = "/tmp/lazyclaw-test-profile"
    env["LAZYCLAW_CDP_PORT"] = "9933"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "upwork_mcp"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as sess:
            await asyncio.wait_for(sess.initialize(), timeout=15)
            return await asyncio.wait_for(sess.list_tools(), timeout=10)


def test_mcp_upwork_lists_tools_over_stdio():
    """Spawns mcp-upwork via stdio and verifies it advertises upwork_* tools."""
    pytest.importorskip("mcp")
    pytest.importorskip("upwork_mcp")

    tools = asyncio.run(_list_tools_with_env_injection())
    names = sorted(t.name for t in tools.tools)

    # Must include the core search tool — non-negotiable for the gig pipeline.
    assert "upwork_search_jobs" in names, f"upwork_search_jobs missing, got {names}"

    # All tool names must be `upwork_` prefixed (collision-safe with our bridge).
    bad = [n for n in names if not n.startswith("upwork_")]
    assert not bad, f"non-prefixed tools leaked: {bad}"

    # Sanity: at least the proposal + messaging surfaces are present.
    expected_subset = {
        "upwork_search_jobs",
        "upwork_get_job_details",
        "upwork_submit_proposal",
        "upwork_get_proposals",
        "upwork_send_message",
        "upwork_get_messages",
        "upwork_get_my_profile",
        "upwork_get_contracts",
    }
    missing = expected_subset - set(names)
    assert not missing, f"missing core tools: {missing}"


def test_env_injection_populates_module_constants():
    """Importing client.py with env vars set picks them up at module load."""
    pytest.importorskip("upwork_mcp")

    # Set BEFORE the (re-)import — module-level constants are computed on import.
    os.environ["LAZYCLAW_BROWSER_PROFILE_DIR"] = "/tmp/lazyclaw-test-profile-x"
    os.environ["LAZYCLAW_CDP_PORT"] = "9933"

    # Force fresh import so module-level constants pick up the env.
    import importlib
    import upwork_mcp.browser.client as client_mod
    importlib.reload(client_mod)

    assert str(client_mod.PROFILE_DIR) == "/tmp/lazyclaw-test-profile-x"
    assert client_mod.CDP_PORT == 9933
