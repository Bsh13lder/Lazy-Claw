"""Tests for build_gateway — registry adapter that wires mcp_call to the real skill
registry.

The registry API (per registry.py + base.py):
  - registry.get(name: str) -> BaseSkill | None
  - skill.execute(user_id: str, params: dict) -> str | ToolResult  (JSON string or ToolResult)

build_gateway wraps these so ChannelGateway receives its injected mcp_call as
  async (tool_name: str, args: dict) -> dict
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from lazyclaw.comms.gateway import build_gateway
from lazyclaw.runtime.tool_result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(expected_tool: str, return_value: object) -> MagicMock:
    """Return a mock registry whose named skill.execute returns `return_value`.

    registry.get routes by name: returns the skill only for expected_tool,
    None for any other name.
    """
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=return_value)
    registry = MagicMock()
    registry.get = MagicMock(side_effect=lambda n: skill if n == expected_tool else None)
    return registry


# ---------------------------------------------------------------------------
# Core contract test (as specified in the task)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_gateway_invokes_registry_tool():
    """build_gateway calls registry.get(tool_name) then skill.execute(user_id, args)."""
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=json.dumps({"status": "sent"}))
    registry = MagicMock()
    registry.get = MagicMock(side_effect=lambda n: skill if n == "whatsapp_send" else None)

    gw = build_gateway(registry, user_id="u1")
    res = await gw.send("whatsapp", "+1", "hi")

    assert res.ok is True
    # The registry was asked for the whatsapp_send skill
    registry.get.assert_called_with("whatsapp_send")
    # The skill was executed with the injected user_id and correct args
    skill.execute.assert_awaited_once()
    call_user_id, call_args = skill.execute.await_args.args
    assert call_user_id == "u1"
    assert call_args["to"] == "+1"
    assert call_args["message"] == "hi"


# ---------------------------------------------------------------------------
# Coercion: skill.execute returns JSON string -> parsed to dict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_json_string_result_is_parsed():
    registry = _make_registry("whatsapp_send", json.dumps({"status": "sent"}))
    gw = build_gateway(registry, user_id="u2")
    res = await gw.send("whatsapp", "+2", "hello")
    assert res.ok is True


@pytest.mark.asyncio
async def test_dict_result_is_passed_through():
    """If the skill already returns a dict (non-standard but safe), it should work."""
    registry = _make_registry("whatsapp_send", {"status": "sent"})
    gw = build_gateway(registry, user_id="u3")
    res = await gw.send("whatsapp", "+3", "direct dict")
    assert res.ok is True


@pytest.mark.asyncio
async def test_non_json_string_wraps_gracefully():
    """A plain non-JSON string is wrapped in a dict without raising."""
    registry = _make_registry("whatsapp_send", "ok")
    gw = build_gateway(registry, user_id="u4")
    res = await gw.send("whatsapp", "+4", "plain")
    # Should not raise; ok=True because status != error/blocked/failed
    assert res.ok is True


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_skill_name_returns_clean_error():
    """If neither the bare lookup nor the MCP base-name fallback finds the
    skill, _call returns a clean error dict — no raise."""
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)  # skill not found
    registry.get_mcp_by_base_name = MagicMock(return_value=None)  # fallback miss too

    gw = build_gateway(registry, user_id="u5")
    res = await gw.send("whatsapp", "+5", "no skill")
    assert res.ok is False
    assert res.error is not None
    assert "unknown tool" in res.error


@pytest.mark.asyncio
async def test_mcp_base_name_fallback_resolves_prefixed_tool():
    """MCP tools register as "mcp_<server>_<tool>" — a bare registry.get
    misses them, and every earlier unit test mocked the bare lookup, so the
    miss only failed live (feedback_test_mocks_masked_production_bug).
    _call must fall back to get_mcp_by_base_name."""
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=json.dumps({"status": "sent"}))
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)  # bare name MISSES — real prod shape
    registry.get_mcp_by_base_name = MagicMock(
        side_effect=lambda base: skill if base == "whatsapp_send" else None,
    )

    gw = build_gateway(registry, user_id="u10")
    res = await gw.send("whatsapp", "+10", "hi")

    assert res.ok is True
    registry.get_mcp_by_base_name.assert_called_with("whatsapp_send")
    skill.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_skill_execute_exception_maps_to_ok_false():
    """If skill.execute raises, ChannelGateway catches it and returns ok=False."""
    skill = MagicMock()
    skill.execute = AsyncMock(side_effect=RuntimeError("network error"))
    registry = MagicMock()
    registry.get = MagicMock(return_value=skill)

    gw = build_gateway(registry, user_id="u6")
    res = await gw.send("whatsapp", "+6", "boom")
    assert res.ok is False
    assert "network error" in (res.error or "")


# ---------------------------------------------------------------------------
# read_thread path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_gateway_read_thread():
    """build_gateway also works for read_thread (uses a different skill name)."""
    messages_payload = {
        "messages": [
            {"sender": "Alice", "content": "hey", "timestamp": "10:00", "is_mine": False}
        ]
    }
    registry = _make_registry("whatsapp_read", json.dumps(messages_payload))

    gw = build_gateway(registry, user_id="u7")
    msgs = await gw.read_thread("whatsapp", "Alice")

    registry.get.assert_called_with("whatsapp_read")
    assert len(msgs) == 1
    assert msgs[0].sender == "Alice"
    assert msgs[0].text == "hey"


# ---------------------------------------------------------------------------
# ToolResult coercion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_result_text_is_extracted():
    """skill.execute returning a ToolResult has its .text payload parsed correctly."""
    tool_result = ToolResult(text=json.dumps({"status": "sent"}))
    registry = _make_registry("whatsapp_send", tool_result)

    gw = build_gateway(registry, user_id="u8")
    res = await gw.send("whatsapp", "+8", "via tool result")
    assert res.ok is True


@pytest.mark.asyncio
async def test_tool_result_non_json_text_wraps_gracefully():
    """ToolResult whose text is not JSON is wrapped in {"status": "sent", "raw": ...}."""
    tool_result = ToolResult(text="delivered")
    registry = _make_registry("whatsapp_send", tool_result)

    gw = build_gateway(registry, user_id="u9")
    res = await gw.send("whatsapp", "+9", "plain text result")
    assert res.ok is True
