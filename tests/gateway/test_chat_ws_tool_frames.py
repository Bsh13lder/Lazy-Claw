"""Frame-shape tests for chat_ws tool_call / tool_result forwarding.

2026-08 tool-observability pass: the agent emits ``args`` (agent.py
tool_call event) and ``result`` (tool_result event), but chat_ws read
``arguments`` / expected ``result`` that was never set — every live
client got ``args={}`` and ``preview=""``. The handler now reads BOTH
key spellings (mirrors runtime/task_runner.py's dual-key readers) and
forwards ``display_name`` as a new frame field. ``name`` semantics are
unchanged — clients match on it.
"""

from __future__ import annotations

from starlette.websockets import WebSocketState

from lazyclaw.gateway.routes.chat_ws import WebSocketCallback
from lazyclaw.runtime.callbacks import AgentEvent


class _FakeWS:
    client_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.frames.append(data)


def _cb() -> tuple[WebSocketCallback, _FakeWS]:
    ws = _FakeWS()
    return WebSocketCallback(ws=ws), ws


# ── tool_call ────────────────────────────────────────────────────────────


async def test_tool_call_agent_shape_args_and_display_name():
    """Agent-emitted metadata uses `args` + `display_name` (agent.py)."""
    cb, ws = _cb()
    await cb.on_event(AgentEvent(
        "tool_call", "Upwork Send",
        {
            "tool": "mcp_uuid_send_message",
            "display_name": "Upwork Send",
            "args": {"to": "James"},
            "tool_call_id": "tc-1",
        },
    ))
    assert ws.frames == [{
        "type": "tool_call",
        "name": "Upwork Send",          # falls back to event.detail — unchanged
        "args": {"to": "James"},        # real args, not {}
        "tool_call_id": "tc-1",
        "display_name": "Upwork Send",
    }]


async def test_tool_call_legacy_arguments_key_still_read():
    cb, ws = _cb()
    await cb.on_event(AgentEvent(
        "tool_call", "web_search",
        {"tool_name": "web_search", "arguments": {"q": "x"}},
    ))
    frame = ws.frames[0]
    assert frame["name"] == "web_search"
    assert frame["args"] == {"q": "x"}
    assert frame["display_name"] is None


async def test_tool_call_no_args_keys_defaults_empty():
    cb, ws = _cb()
    await cb.on_event(AgentEvent("tool_call", "browser", {"tool": "browser"}))
    assert ws.frames[0]["args"] == {}


async def test_bg_tool_call_carries_display_name_and_args():
    cb, ws = _cb()
    await cb.on_event(AgentEvent(
        "tool_call", "Web Search",
        {
            "bg_task_id": "bg-1",
            "bg_task_name": "research",
            "tool": "web_search",
            "display_name": "Web Search",
            "args": {"q": "x"},
            "tool_call_id": "tc-9",
        },
    ))
    assert ws.frames == [{
        "type": "bg_tool_call",
        "task_id": "bg-1",
        "task_name": "research",
        "name": "Web Search",
        "args": {"q": "x"},
        "tool_call_id": "tc-9",
        "display_name": "Web Search",
    }]


# ── tool_result ──────────────────────────────────────────────────────────


async def test_tool_result_reads_result_key_and_truncates_200():
    cb, ws = _cb()
    await cb.on_event(AgentEvent(
        "tool_result", "Web Search",
        {
            "tool": "web_search",
            "display_name": "Web Search",
            "result": "R" * 300,
            "tool_call_id": "tc-2",
        },
    ))
    assert ws.frames == [{
        "type": "tool_result",
        "name": "Web Search",
        "preview": "R" * 200,
        "tool_call_id": "tc-2",
        "display_name": "Web Search",
    }]


async def test_tool_result_legacy_preview_key_still_read():
    cb, ws = _cb()
    await cb.on_event(AgentEvent(
        "tool_result", "browser", {"tool_name": "browser", "preview": "cached"},
    ))
    frame = ws.frames[0]
    assert frame["preview"] == "cached"
    assert frame["display_name"] is None


async def test_tool_result_no_result_keys_defaults_empty():
    cb, ws = _cb()
    await cb.on_event(AgentEvent("tool_result", "browser", {"tool": "browser"}))
    assert ws.frames[0]["preview"] == ""


async def test_bg_tool_result_carries_display_name_and_preview():
    cb, ws = _cb()
    await cb.on_event(AgentEvent(
        "tool_result", "Web Search",
        {
            "bg_task_id": "bg-1",
            "bg_task_name": "research",
            "tool": "web_search",
            "display_name": "Web Search",
            "result": "found 10 results",
            "tool_call_id": "tc-9",
        },
    ))
    assert ws.frames == [{
        "type": "bg_tool_result",
        "task_id": "bg-1",
        "task_name": "research",
        "name": "Web Search",
        "preview": "found 10 results",
        "tool_call_id": "tc-9",
        "display_name": "Web Search",
    }]
