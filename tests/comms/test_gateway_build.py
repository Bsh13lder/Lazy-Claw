"""Tests for build_gateway — registry adapter that wires mcp_call to the real skill
registry.

The real registry API (per registry.py):
  - registry.get_mcp_by_base_name(base_name: str) -> BaseSkill | None
      Finds the first MCP skill registered as mcp_<server_id>_<base_name>.
      This is the PRIMARY resolver for all 6 channel tools (whatsapp_send,
      whatsapp_read, email_send, email_search, instagram_send_dm,
      instagram_read_dms) which are bridged as MCP tools.
  - registry.get(name: str) -> BaseSkill | None
      Exact-match fallback for native (non-MCP) skills.
  - skill.execute(user_id: str, params: dict) -> str | ToolResult  (JSON string or ToolResult)

build_gateway._call MUST:
  1. Call get_mcp_by_base_name(tool_name) first.
  2. Only fall back to get(tool_name) if the MCP resolver returns None.
  3. Return a clean error dict when both return None.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, call

from lazyclaw.comms.gateway import build_gateway
from lazyclaw.runtime.tool_result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_mcp(base_name: str, return_value: object) -> MagicMock:
    """Registry whose get_mcp_by_base_name resolves `base_name` to a skill.

    get() (exact-match) always returns None, so we can assert MCP path is taken.
    """
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=return_value)
    registry = MagicMock()
    registry.get_mcp_by_base_name = MagicMock(
        side_effect=lambda n: skill if n == base_name else None
    )
    registry.get = MagicMock(return_value=None)  # exact-match always misses
    return registry


def _make_registry_native(tool_name: str, return_value: object) -> MagicMock:
    """Registry where get_mcp_by_base_name always returns None but get() resolves
    `tool_name` exactly. Used to test the native-skill fallback path."""
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=return_value)
    registry = MagicMock()
    registry.get_mcp_by_base_name = MagicMock(return_value=None)
    registry.get = MagicMock(side_effect=lambda n: skill if n == tool_name else None)
    return registry


# ---------------------------------------------------------------------------
# Resolution contract: exact-match FIRST, MCP base-name SECOND (native primacy)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_gateway_passes_user_id_and_args_to_skill():
    """The skill is executed with the correct user_id and whatsapp args."""
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=json.dumps({"status": "sent"}))
    registry = MagicMock()
    registry.get_mcp_by_base_name = MagicMock(
        side_effect=lambda n: skill if n == "whatsapp_send" else None
    )
    registry.get = MagicMock(return_value=None)

    gw = build_gateway(registry, user_id="u1")
    res = await gw.send("whatsapp", "+1", "hi")

    assert res.ok is True
    skill.execute.assert_awaited_once()
    call_user_id, call_args = skill.execute.await_args.args
    assert call_user_id == "u1"
    assert call_args["to"] == "+1"
    assert call_args["message"] == "hi"


# ---------------------------------------------------------------------------
# FALLBACK contract: native skills (no MCP prefix) resolved by get()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_native_skill_wins_over_mcp_base_name():
    """NATIVE PRIMACY: a skill registered under its bare name resolves through
    the exact-match get() FIRST, and the MCP base-name resolver is never
    consulted. An MCP server exposing a same-named tool must never shadow the
    native skill — that collision hung the loop once already."""
    registry = _make_registry_native("whatsapp_send", json.dumps({"status": "sent"}))

    gw = build_gateway(registry, user_id="u_native")
    res = await gw.send("whatsapp", "+2", "native")

    assert res.ok is True
    registry.get.assert_called_with("whatsapp_send")
    registry.get_mcp_by_base_name.assert_not_called()


# ---------------------------------------------------------------------------
# Error path: both resolvers return None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_skill_returns_clean_error():
    """If both get_mcp_by_base_name and get return None, _call returns a clean
    error dict and SendResult(ok=False) is surfaced — no raise."""
    registry = MagicMock()
    registry.get_mcp_by_base_name = MagicMock(return_value=None)
    registry.get = MagicMock(return_value=None)

    gw = build_gateway(registry, user_id="u5")
    res = await gw.send("whatsapp", "+5", "no skill")
    assert res.ok is False
    assert res.error is not None
    assert "unknown tool" in res.error


@pytest.mark.asyncio
async def test_registry_without_get_mcp_by_base_name_falls_back_gracefully():
    """If the registry object doesn't expose get_mcp_by_base_name at all (e.g. a
    minimal mock or legacy object), build_gateway falls back to get() only."""
    skill = MagicMock()
    skill.execute = AsyncMock(return_value=json.dumps({"status": "sent"}))
    registry = MagicMock(spec=["get"])   # only has .get — no get_mcp_by_base_name
    registry.get = MagicMock(return_value=skill)

    gw = build_gateway(registry, user_id="u_legacy")
    res = await gw.send("whatsapp", "+3", "legacy")
    assert res.ok is True


# ---------------------------------------------------------------------------
# Coercion: skill.execute returns JSON string -> parsed to dict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_json_string_result_is_parsed():
    registry = _make_registry_mcp("whatsapp_send", json.dumps({"status": "sent"}))
    gw = build_gateway(registry, user_id="u2")
    res = await gw.send("whatsapp", "+2", "hello")
    assert res.ok is True


@pytest.mark.asyncio
async def test_dict_result_is_passed_through():
    """If the skill already returns a dict (non-standard but safe), it should work."""
    registry = _make_registry_mcp("whatsapp_send", {"status": "sent"})
    gw = build_gateway(registry, user_id="u3")
    res = await gw.send("whatsapp", "+3", "direct dict")
    assert res.ok is True


@pytest.mark.asyncio
async def test_non_json_string_wraps_gracefully():
    """A plain non-JSON string is wrapped in a dict without raising."""
    registry = _make_registry_mcp("whatsapp_send", "ok")
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
    registry.get_mcp_by_base_name = MagicMock(return_value=skill)
    registry.get = MagicMock(return_value=None)

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
    registry = _make_registry_mcp("whatsapp_read", json.dumps(messages_payload))

    gw = build_gateway(registry, user_id="u7")
    res = await gw.read_thread("whatsapp", "Alice")

    registry.get.assert_called_with("whatsapp_read")
    assert res.ok is True
    assert len(res.messages) == 1
    assert res.messages[0].sender == "Alice"
    assert res.messages[0].text == "hey"


@pytest.mark.asyncio
async def test_build_gateway_read_thread_unknown_tool_is_failure():
    """The _call adapter returns {"status": "error", "error": "unknown tool: ..."}
    (it never raises) when the registry has no matching skill — e.g. while the
    MCP container is restarting. read_thread must surface that as ok=False,
    not as an empty thread."""
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    registry.get_mcp_by_base_name = MagicMock(return_value=None)

    gw = build_gateway(registry, user_id="u8")
    res = await gw.read_thread("whatsapp", "Alice")

    assert res.ok is False
    assert "unknown tool" in (res.error or "")


# ---------------------------------------------------------------------------
# ToolResult coercion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_result_text_is_extracted():
    """skill.execute returning a ToolResult has its .text payload parsed correctly."""
    tool_result = ToolResult(text=json.dumps({"status": "sent"}))
    registry = _make_registry_mcp("whatsapp_send", tool_result)

    gw = build_gateway(registry, user_id="u8")
    res = await gw.send("whatsapp", "+8", "via tool result")
    assert res.ok is True


@pytest.mark.asyncio
async def test_tool_result_non_json_text_wraps_gracefully():
    """ToolResult whose text is not JSON is wrapped in {"status": "sent", "raw": ...}."""
    tool_result = ToolResult(text="delivered")
    registry = _make_registry_mcp("whatsapp_send", tool_result)

    gw = build_gateway(registry, user_id="u9")
    res = await gw.send("whatsapp", "+9", "plain text result")
    assert res.ok is True


# ---------------------------------------------------------------------------
# _looks_like_error unit tests
# ---------------------------------------------------------------------------

from lazyclaw.comms.gateway import _looks_like_error


@pytest.mark.parametrize("text", [
    "Account  not configured. Use email_setup first.",
    "Account myemail@example.com not configured. Use email_setup first.",
    "No Instagram session found. Run instagram_setup first.",
    "no session for @alice",
    "Setup first to continue.",
    "Operation failed: invalid credentials",
    "Could not connect to SMTP server",
    "Error in email_send: timeout",
    "ERROR: missing credentials",
])
def test_looks_like_error_detects_failures(text: str):
    """Known MCP error strings are correctly identified as failures."""
    assert _looks_like_error(text) is True, f"Should detect error in: {text!r}"


@pytest.mark.parametrize("text", [
    "Sent to user@example.com",
    "Sent to @targetuser",
    "Connected as alice@example.com",
    "Deleted 3 email(s) from INBOX.",
    "Labeled 5 email(s) with 'Financial' (kept in INBOX).",
    "Message delivered.",
])
def test_looks_like_error_passes_genuine_success(text: str):
    """Genuine success strings are NOT flagged as errors."""
    assert _looks_like_error(text) is False, f"Should NOT detect error in: {text!r}"
