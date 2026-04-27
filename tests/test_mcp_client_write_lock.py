"""MCPClient must serialize all writes to its underlying _write_stream.

The MCP Python SDK's `BaseSession._write_stream.send()` is unprotected by
any lock. Concurrent `call_tool()` callers will interleave their JSON-RPC
writes on stdin, corrupt the stream, and the server will close the session
("Connection closed"). Our `MCPClient` must add a per-instance asyncio.Lock
so the wire stays well-formed.

Diagnosed 2026-04-27 — see plans/rosy-meandering-parnas.md.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from lazyclaw.mcp.client import MCPClient


@dataclass
class _Interval:
    start: float
    end: float


class _FakeSession:
    """Stand-in for an MCP ClientSession. Records every call_tool / list_tools
    invocation as a (start, end) interval. Serialized callers must produce
    non-overlapping intervals when sorted by start."""

    def __init__(self, work_seconds: float = 0.05) -> None:
        self._work = work_seconds
        self.intervals: list[_Interval] = []
        self._lock = asyncio.Lock()  # only protects the intervals list itself

    async def call_tool(self, name: str, arguments: dict) -> object:
        start = time.monotonic()
        await asyncio.sleep(self._work)
        end = time.monotonic()
        async with self._lock:
            self.intervals.append(_Interval(start=start, end=end))
        return _FakeResult()

    async def list_tools(self) -> object:
        start = time.monotonic()
        await asyncio.sleep(self._work)
        end = time.monotonic()
        async with self._lock:
            self.intervals.append(_Interval(start=start, end=end))
        return _FakeListResult()


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResult:
    """Bare-minimum result object to satisfy MCPClient.call_tool's parser."""

    isError = False

    def __init__(self):
        self.content = [_FakeBlock("ok")]


class _FakeListResult:
    """Mimics ListToolsResult shape that MCPClient.list_tools iterates over."""

    def __init__(self):
        self.tools: list = []


def _build_client(session: _FakeSession) -> MCPClient:
    """Hand-construct a connected MCPClient with a fake session."""
    client = MCPClient(
        server_id="srv-test",
        name="fake",
        transport="stdio",
        config={},
    )
    client._session = session  # type: ignore[attr-defined]
    return client


def _intervals_are_non_overlapping(intervals: list[_Interval]) -> bool:
    """Sorted by start, every i+1 must begin at-or-after i's end."""
    sorted_iv = sorted(intervals, key=lambda iv: iv.start)
    for prev, nxt in zip(sorted_iv, sorted_iv[1:]):
        # Allow a tiny float tolerance — clocks aren't perfect
        if nxt.start + 1e-4 < prev.end:
            return False
    return True


@pytest.mark.asyncio
async def test_concurrent_call_tool_serializes():
    """8 concurrent call_tool() calls on one client must execute one-at-a-time."""
    session = _FakeSession(work_seconds=0.04)
    client = _build_client(session)

    await asyncio.gather(*(client.call_tool("t", {}) for _ in range(8)))

    assert len(session.intervals) == 8
    assert _intervals_are_non_overlapping(session.intervals), (
        "Concurrent calls overlapped — write lock is missing or broken. "
        "Intervals: " + str([(round(iv.start, 4), round(iv.end, 4)) for iv in session.intervals])
    )


@pytest.mark.asyncio
async def test_two_clients_run_concurrently():
    """Locks are per-instance — calls on different clients can overlap."""
    session_a = _FakeSession(work_seconds=0.05)
    session_b = _FakeSession(work_seconds=0.05)
    client_a = _build_client(session_a)
    client_b = _build_client(session_b)

    start = time.monotonic()
    await asyncio.gather(
        client_a.call_tool("t", {}),
        client_b.call_tool("t", {}),
    )
    elapsed = time.monotonic() - start

    # If they were serialized across clients, elapsed >= 0.10s.
    # Concurrent across separate clients: elapsed ≈ 0.05s.
    assert elapsed < 0.09, (
        f"Two clients should run in parallel, got {elapsed*1000:.0f}ms — "
        "lock leaked across instances?"
    )


@pytest.mark.asyncio
async def test_list_tools_also_serializes():
    """list_tools shares the same wire and must use the same lock."""
    session = _FakeSession(work_seconds=0.04)
    client = _build_client(session)

    await asyncio.gather(
        client.list_tools(),
        client.list_tools(),
        client.list_tools(),
    )
    assert len(session.intervals) == 3
    assert _intervals_are_non_overlapping(session.intervals)


@pytest.mark.asyncio
async def test_mixed_call_and_list_serialize_together():
    """call_tool and list_tools share the wire — both go through one lock."""
    session = _FakeSession(work_seconds=0.04)
    client = _build_client(session)

    await asyncio.gather(
        client.call_tool("t", {}),
        client.list_tools(),
        client.call_tool("t2", {}),
        client.list_tools(),
    )
    assert len(session.intervals) == 4
    assert _intervals_are_non_overlapping(session.intervals)
