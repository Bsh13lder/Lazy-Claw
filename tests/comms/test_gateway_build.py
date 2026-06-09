"""Tests for build_gateway — registry adapter that wires mcp_call to the real skill
registry.

The registry API (per registry.py + base.py):
  - registry.get(name: str) -> BaseSkill | None
  - skill.execute(user_id: str, params: dict) -> str  (JSON string)

build_gateway wraps these so ChannelGateway receives its injected mcp_call as
  async (tool_name: str, args: dict) -> dict
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from lazyclaw.comms.gateway import build_gateway


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(tool_name: str, return_value: object) -> MagicMock:
    """Return a mock registry whose named skill.execute returns `return_value`."""
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=return_value)
    registry = MagicMock()
    registry.get = MagicMock(return_value=skill)
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
    registry.get = MagicMock(return_value=skill)

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
    skill = MagicMock()
    skill.execute = AsyncMock(return_value={"status": "sent"})
    registry = MagicMock()
    registry.get = MagicMock(return_value=skill)

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
async def test_unknown_skill_name_raises_via_gateway_error_path():
    """If the skill is not found, execute raises; ChannelGateway maps to ok=False."""
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)  # skill not found

    gw = build_gateway(registry, user_id="u5")
    res = await gw.send("whatsapp", "+5", "no skill")
    assert res.ok is False


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
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=json.dumps(messages_payload))
    registry = MagicMock()
    registry.get = MagicMock(return_value=skill)

    gw = build_gateway(registry, user_id="u7")
    msgs = await gw.read_thread("whatsapp", "Alice")

    registry.get.assert_called_with("whatsapp_read")
    assert len(msgs) == 1
    assert msgs[0].sender == "Alice"
    assert msgs[0].text == "hey"
