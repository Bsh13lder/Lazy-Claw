"""MCPToolSkill must cap concurrent calls per server_id.

Without this cap, parallel EXPLORE specialists hammered the mcp-scraper
stdio subprocess and OOM-crashed Chromium (5 restarts in 30 min on the
real Hirossa salon-emails batch). The semaphore keys by server_id so
unrelated MCP servers (workspace-mcp, mcp-instagram, etc.) don't share
the cap.
"""

from __future__ import annotations

import asyncio

import pytest

from lazyclaw.mcp.bridge import (
    MCPToolSkill,
    _get_call_semaphore,
    _mcp_call_concurrency,
    _server_call_semaphores,
)


class _FakeMCPClient:
    def __init__(self, server_id: str, name: str = "fake-server"):
        self.server_id = server_id
        self.name = name
        self.in_flight = 0
        self.peak = 0
        self._lock = asyncio.Lock()

    async def call_tool(self, tool_name: str, params: dict) -> str:
        async with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        # Simulate Playwright cold-start + crawl latency
        await asyncio.sleep(0.05)
        async with self._lock:
            self.in_flight -= 1
        return "ok"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Each test starts with a clean semaphore registry + default cap."""
    _server_call_semaphores.clear()
    monkeypatch.delenv("LAZYCLAW_MCP_CALL_CONCURRENCY", raising=False)
    yield
    _server_call_semaphores.clear()


def test_default_cap_is_four(monkeypatch):
    monkeypatch.delenv("LAZYCLAW_MCP_CALL_CONCURRENCY", raising=False)
    assert _mcp_call_concurrency() == 4


def test_env_override(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_MCP_CALL_CONCURRENCY", "2")
    assert _mcp_call_concurrency() == 2


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_MCP_CALL_CONCURRENCY", "not-a-number")
    assert _mcp_call_concurrency() == 4


@pytest.mark.asyncio
async def test_calls_are_capped_per_server():
    """Eight parallel calls on one server stay below the default cap of 4."""
    client = _FakeMCPClient(server_id="srv-A")
    skill = MCPToolSkill(
        client=client,  # type: ignore[arg-type]
        tool_name="anything",
        tool_description="d",
        tool_schema={"type": "object", "properties": {}},
    )
    await asyncio.gather(*(skill.execute("u1", {}) for _ in range(8)))
    assert client.peak <= 4, f"semaphore leaked: peak={client.peak}"


@pytest.mark.asyncio
async def test_env_var_caps_concurrency(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_MCP_CALL_CONCURRENCY", "2")
    client = _FakeMCPClient(server_id="srv-B")
    skill = MCPToolSkill(
        client=client,  # type: ignore[arg-type]
        tool_name="anything",
        tool_description="d",
        tool_schema={"type": "object", "properties": {}},
    )
    await asyncio.gather(*(skill.execute("u1", {}) for _ in range(8)))
    assert client.peak <= 2, f"env-var cap not honored: peak={client.peak}"


@pytest.mark.asyncio
async def test_per_server_isolation():
    """Two distinct server_ids should each have their own semaphore — running
    in parallel on both should NOT throttle each other."""
    client_a = _FakeMCPClient(server_id="srv-X")
    client_b = _FakeMCPClient(server_id="srv-Y")
    skill_a = MCPToolSkill(
        client=client_a, tool_name="t", tool_description="d",  # type: ignore[arg-type]
        tool_schema={"type": "object", "properties": {}},
    )
    skill_b = MCPToolSkill(
        client=client_b, tool_name="t", tool_description="d",  # type: ignore[arg-type]
        tool_schema={"type": "object", "properties": {}},
    )
    coros = [skill_a.execute("u1", {}) for _ in range(4)]
    coros += [skill_b.execute("u1", {}) for _ in range(4)]
    await asyncio.gather(*coros)
    # Each server got its own semaphore — both saw up to 4 in-flight,
    # but neither was blocked by the OTHER server's cap.
    assert client_a.peak >= 1
    assert client_b.peak >= 1
    # Distinct semaphore objects (not the same instance shared)
    assert _get_call_semaphore("srv-X") is not _get_call_semaphore("srv-Y")
