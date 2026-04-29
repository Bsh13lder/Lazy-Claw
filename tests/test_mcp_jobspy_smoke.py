"""Smoke test: spawn mcp_jobspy over stdio and verify list_tools.

No network. Only checks the MCP plumbing — that the server starts,
advertises the search_jobs tool, and the tool's schema is well-formed.
"""

from __future__ import annotations

import asyncio
import sys

import pytest


async def _list_tools_over_stdio():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_jobspy"],
        env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as sess:
            await asyncio.wait_for(sess.initialize(), timeout=10)
            return await asyncio.wait_for(sess.list_tools(), timeout=5)


def test_mcp_jobspy_lists_search_jobs_over_stdio():
    """Spawns mcp_jobspy and verifies it advertises search_jobs."""
    pytest.importorskip("mcp")
    pytest.importorskip("mcp_jobspy")

    tools = asyncio.run(_list_tools_over_stdio())

    names = [t.name for t in tools.tools]
    assert names == ["search_jobs"]

    schema = tools.tools[0].inputSchema
    assert schema["required"] == ["search_term"]
    props = schema["properties"]
    for key in ("search_term", "location", "site_name", "results_wanted",
                "hours_old", "is_remote"):
        assert key in props, f"missing schema property: {key}"
