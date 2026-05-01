"""MCPClient must self-heal when its stdio subprocess crashes mid-call.

Background: the official mcp Python SDK does not detect dead stdio
subprocesses (modelcontextprotocol/python-sdk#396). Until the next
call_tool hits a closed pipe, MCPClient.is_connected stays True and
PooledMCPToolSkill burns through "shards" forever. db7dd27 collapsed
mcp-scraper to a single subprocess — without respawn the pool now
stays "exhausted" until container restart.

This test pins the new contract:
- ``is_alive`` rejects a client whose streams are closed even when
  ``_session`` is still set.
- A transport error inside ``call_tool`` triggers exactly one respawn
  attempt and one retry.
- The respawn budget is bounded: after _MAX_RESPAWN_ATTEMPTS failures
  the client is marked dead and stays dead.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lazyclaw.mcp.client import (
    _MAX_RESPAWN_ATTEMPTS,
    MCPClient,
    _is_transport_error,
)


def _make_client() -> MCPClient:
    return MCPClient(
        server_id="srv-test",
        name="mcp-test",
        transport="stdio",
        config={},
    )


def _attach_fake_session(client: MCPClient, call_tool_fn) -> None:
    """Wire up the minimum state so ``is_alive`` returns True."""
    client._session = SimpleNamespace(call_tool=call_tool_fn)
    # is_alive checks: ctx_task running, streams not closed
    fake_task = asyncio.get_event_loop().create_future()  # never done
    client._ctx_task = fake_task  # type: ignore[assignment]
    client._read_stream = SimpleNamespace(_closed=False)
    client._write_stream = SimpleNamespace(_closed=False)


def _kill_subprocess(client: MCPClient) -> None:
    """Simulate the subprocess dying — streams flip to closed."""
    client._read_stream._closed = True  # type: ignore[union-attr]
    client._write_stream._closed = True  # type: ignore[union-attr]


def test_is_transport_error_classifies():
    assert _is_transport_error(BrokenPipeError("pipe gone"))
    assert _is_transport_error(ConnectionError("dropped"))
    assert _is_transport_error(OSError("EBADF"))
    assert _is_transport_error(RuntimeError("Stream is closed"))
    assert not _is_transport_error(ValueError("nope"))
    assert not _is_transport_error(RuntimeError("something else"))
    # anyio names are matched by class-name, not import (anyio is a
    # transitive dep — don't pull it in just for this guard).
    fake_anyio = type("BrokenResourceError", (Exception,), {})()
    assert _is_transport_error(fake_anyio)


@pytest.mark.asyncio
async def test_is_alive_rejects_closed_streams():
    """Even when _session is non-None, closed streams mean the child died."""
    client = _make_client()
    _attach_fake_session(client, AsyncMock())
    assert client.is_alive is True
    _kill_subprocess(client)
    assert client.is_alive is False
    # Closed streams must not register as "connected enough to call".
    assert client.is_connected is True  # backwards-compat property unchanged


@pytest.mark.asyncio
async def test_call_tool_respawns_on_transport_error(monkeypatch):
    """Single transport error → one respawn → retry succeeds."""
    client = _make_client()
    call_count = {"n": 0}

    async def call_tool_fn(name, arguments):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise BrokenPipeError("subprocess died mid-call")
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="ok-after-respawn")],
        )

    _attach_fake_session(client, call_tool_fn)

    respawn_calls = {"n": 0}

    async def fake_respawn() -> bool:
        respawn_calls["n"] += 1
        # Pretend the new subprocess is alive — re-attach the session.
        _attach_fake_session(client, call_tool_fn)
        return True

    monkeypatch.setattr(client, "respawn", fake_respawn)

    result = await client.call_tool("scrape", {"url": "x"})

    assert result == "ok-after-respawn"
    assert call_count["n"] == 2  # original + retry
    assert respawn_calls["n"] == 1


@pytest.mark.asyncio
async def test_call_tool_propagates_non_transport_errors(monkeypatch):
    """A plain ValueError must NOT trigger a respawn."""
    client = _make_client()

    async def call_tool_fn(name, arguments):
        raise ValueError("bad arguments")

    _attach_fake_session(client, call_tool_fn)

    respawn_calls = {"n": 0}

    async def fake_respawn() -> bool:
        respawn_calls["n"] += 1
        return True

    monkeypatch.setattr(client, "respawn", fake_respawn)

    with pytest.raises(ValueError, match="bad arguments"):
        await client.call_tool("x", {})
    assert respawn_calls["n"] == 0


@pytest.mark.asyncio
async def test_respawn_budget_marks_client_dead(monkeypatch):
    """After _MAX_RESPAWN_ATTEMPTS failed reconnects, client is_dead = True."""
    client = _make_client()

    # Stub disconnect/connect so we don't actually spawn a subprocess.
    async def noop():
        return None

    async def failing_connect():
        # Always fails — simulates an unrecoverable child (binary missing,
        # port permanently taken, etc.).
        raise RuntimeError("connect failed")

    monkeypatch.setattr(client, "disconnect", noop)
    monkeypatch.setattr(client, "connect", failing_connect)
    # Skip the backoff sleep so the test runs fast.
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    for _ in range(_MAX_RESPAWN_ATTEMPTS):
        ok = await client.respawn()
        assert ok is False
    # One more call should short-circuit on _dead and NOT attempt connect().
    assert client.is_dead is True
    assert await client.respawn() is False


@pytest.mark.asyncio
async def test_concurrent_respawn_collapses_to_one(monkeypatch):
    """9 callers simultaneously triggering respawn must run exactly one
    teardown+reconnect — the lock + is_alive recheck handle the herd."""
    client = _make_client()
    connect_calls = {"n": 0}

    async def fake_disconnect():
        return None

    async def fake_connect():
        connect_calls["n"] += 1
        # First respawn brings the client back to alive.
        _attach_fake_session(client, AsyncMock())

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(client, "disconnect", fake_disconnect)
    monkeypatch.setattr(client, "connect", fake_connect)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    # All start with a dead client.
    results = await asyncio.gather(*(client.respawn() for _ in range(9)))

    assert all(results), "every caller should see a live client"
    assert connect_calls["n"] == 1, (
        f"expected 1 connect, got {connect_calls['n']} — "
        "the herd-collapse recheck inside _respawn_lock is broken"
    )
