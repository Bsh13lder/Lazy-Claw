"""MCPClient.call_tool must run concurrent invocations in parallel.

Background: prior to 2026-04-29, `call_tool` wrapped the entire
`_session.call_tool(...)` await in a per-instance asyncio.Lock. The
intent was to prevent JSON-RPC byte interleaving on stdin, but the MCP
SDK already serializes wire writes via a single `stdin_writer` task
(mcp/client/stdio/__init__.py:166) and demultiplexes responses by
request_id (mcp/shared/session.py:_response_streams). Holding the lock
queued every parallel caller behind the slowest scrape — that was the
9-subagent salon-batch timeout. The lock has been removed from
`call_tool` (it stays around `list_tools` for cache-write safety).

This test pins the contract: 4 concurrent `call_tool`s on one client
finish in roughly one tool-duration, not four.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lazyclaw.mcp.client import MCPClient


def _make_client_with_fake_session(call_tool_fn) -> MCPClient:
    client = MCPClient(
        server_id="srv-test",
        name="mcp-test",
        transport="stdio",
        config={},
    )
    client._session = SimpleNamespace(call_tool=call_tool_fn)
    return client


@pytest.mark.asyncio
async def test_concurrent_call_tool_runs_in_parallel():
    """4 calls × 0.5 s each should finish in well under 1.0 s when parallel.

    Serial execution would take ~2.0 s — this test fails if a future
    change re-introduces a whole-call lock around `_session.call_tool`.
    """
    async def slow_call(name, arguments):
        await asyncio.sleep(0.5)
        return SimpleNamespace(isError=False, content=[
            SimpleNamespace(text=f"ok:{name}"),
        ])

    client = _make_client_with_fake_session(slow_call)

    start = time.monotonic()
    results = await asyncio.gather(
        client.call_tool("a", {}),
        client.call_tool("b", {}),
        client.call_tool("c", {}),
        client.call_tool("d", {}),
    )
    elapsed = time.monotonic() - start

    assert results == ["ok:a", "ok:b", "ok:c", "ok:d"]
    # Generous bound (0.9 s) to absorb scheduler jitter on CI; serial
    # would be ~2.0 s.
    assert elapsed < 0.9, (
        f"Expected concurrent execution (~0.5s), got {elapsed:.2f}s — "
        "is _call_lock back around call_tool?"
    )


@pytest.mark.asyncio
async def test_call_tool_propagates_isError():
    async def err_call(name, arguments):
        return SimpleNamespace(isError=True, content=[
            SimpleNamespace(text="boom"),
        ])

    client = _make_client_with_fake_session(err_call)
    out = await client.call_tool("x", {})
    assert "[MCP ERROR]" in out
    assert "boom" in out


@pytest.mark.asyncio
async def test_call_tool_empty_result_returns_no_data_marker():
    async def empty_call(name, arguments):
        return SimpleNamespace(isError=False, content=[
            SimpleNamespace(text=""),
        ])

    client = _make_client_with_fake_session(empty_call)
    out = await client.call_tool("x", {})
    assert "[NO DATA]" in out
