"""Tests for ChannelGateway.send — injected mcp_call path.

These test the _DISPATCH table dispatch, not registry resolution (that is
covered by test_gateway_build.py). mcp_call is injected directly.

Arg shapes verified against real MCP server inputSchemas:
  whatsapp_send:     to (required) + message (required)
  email_send:        to + body + email (sender, defaults "")
                     + subject (defaults "(no subject)")
  instagram_send_dm: to_username + message + username (account, defaults "")
"""
import pytest
from unittest.mock import AsyncMock
from lazyclaw.comms.gateway import ChannelGateway

@pytest.mark.asyncio
async def test_send_dispatches_to_whatsapp_tool():
    call_tool = AsyncMock(return_value={"status": "sent"})
    gw = ChannelGateway(mcp_call=call_tool)
    res = await gw.send("whatsapp", "+34600000000", "hello")
    assert res.ok is True
    name, args = call_tool.await_args.args
    assert name == "whatsapp_send"
    assert args["to"] == "+34600000000"
    assert args["message"] == "hello"
    # WhatsApp has no extra args — confirm no unexpected keys
    assert set(args.keys()) == {"to", "message"}


@pytest.mark.asyncio
async def test_send_dispatches_to_email_tool_with_defaults():
    """email_send includes default email (sender) and subject from extra_send_args."""
    call_tool = AsyncMock(return_value={"status": "sent"})
    gw = ChannelGateway(mcp_call=call_tool)
    res = await gw.send("email", "user@example.com", "Hello there")
    assert res.ok is True
    name, args = call_tool.await_args.args
    assert name == "email_send"
    assert args["to"] == "user@example.com"
    assert args["body"] == "Hello there"
    # Default sender email and subject are merged in
    assert "email" in args
    assert "subject" in args
    assert args["subject"] == "(no subject)"


@pytest.mark.asyncio
async def test_send_dispatches_to_instagram_tool_with_account():
    """instagram_send_dm includes username (account) from extra_send_args."""
    call_tool = AsyncMock(return_value={"status": "sent"})
    gw = ChannelGateway(mcp_call=call_tool)
    res = await gw.send("instagram", "targetuser", "hey!")
    assert res.ok is True
    name, args = call_tool.await_args.args
    assert name == "instagram_send_dm"
    assert args["to_username"] == "targetuser"
    assert args["message"] == "hey!"
    # username (own account) must be present
    assert "username" in args


@pytest.mark.asyncio
async def test_send_unknown_channel_errors():
    gw = ChannelGateway(mcp_call=AsyncMock())
    res = await gw.send("carrier-pigeon", "x", "y")
    assert res.ok is False and "unsupported" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_send_telegram_unsupported_gracefully():
    """telegram is a valid channel name but intentionally not in _DISPATCH."""
    gw = ChannelGateway(mcp_call=AsyncMock())
    res = await gw.send("telegram", "@user", "hi")
    assert res.ok is False
    assert "unsupported" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_send_blocked_status_is_failure():
    call_tool = AsyncMock(return_value={"status": "blocked", "offending_token": "http://x"})
    gw = ChannelGateway(mcp_call=call_tool)
    res = await gw.send("whatsapp", "+1", "see http://x")
    assert res.ok is False


@pytest.mark.asyncio
async def test_send_exception_is_typed_failure():
    call_tool = AsyncMock(side_effect=RuntimeError("mcp down"))
    gw = ChannelGateway(mcp_call=call_tool)
    res = await gw.send("whatsapp", "+1", "hi")
    assert res.ok is False and "mcp down" in (res.error or "")


# ---------------------------------------------------------------------------
# Fix 2: gateway must NOT mask error strings as ok=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_not_configured_error_string_maps_to_ok_false():
    """The exact error string returned by email_send when no account is configured
    must NOT be wrapped as ok=True by the gateway's non-JSON coercion path."""
    from lazyclaw.comms.gateway import build_gateway
    from unittest.mock import AsyncMock, MagicMock

    error_text = "Account  not configured. Use email_setup first."

    skill = MagicMock()
    skill.execute = AsyncMock(return_value=error_text)

    registry_obj = MagicMock()
    registry_obj.get_mcp_by_base_name = MagicMock(return_value=skill)
    registry_obj.get = MagicMock(return_value=None)

    gw = build_gateway(registry_obj, user_id="u_test")
    res = await gw.send("email", "recipient@example.com", "hello")
    assert res.ok is False, (
        "Gateway must return ok=False when the MCP tool returns an error string; "
        f"got ok={res.ok!r}"
    )


@pytest.mark.asyncio
async def test_instagram_no_session_error_string_maps_to_ok_false():
    """instagram_send_dm error 'No session found. Run instagram_setup first.'
    must NOT be reported as ok=True by the gateway."""
    from lazyclaw.comms.gateway import build_gateway
    from unittest.mock import AsyncMock, MagicMock

    error_text = "No session found. Run instagram_setup first."

    skill = MagicMock()
    skill.execute = AsyncMock(return_value=error_text)

    registry_obj = MagicMock()
    registry_obj.get_mcp_by_base_name = MagicMock(return_value=skill)
    registry_obj.get = MagicMock(return_value=None)

    gw = build_gateway(registry_obj, user_id="u_ig")
    res = await gw.send("instagram", "targetuser", "hey")
    assert res.ok is False


@pytest.mark.asyncio
async def test_genuine_success_string_not_mistaken_for_error():
    """A genuine success string like 'Sent to user@example.com' must not be
    misdetected as an error (it contains no _ERROR_MARKERS)."""
    from lazyclaw.comms.gateway import build_gateway
    from unittest.mock import AsyncMock, MagicMock

    success_text = "Sent to user@example.com"

    skill = MagicMock()
    skill.execute = AsyncMock(return_value=success_text)

    registry_obj = MagicMock()
    registry_obj.get_mcp_by_base_name = MagicMock(return_value=skill)
    registry_obj.get = MagicMock(return_value=None)

    gw = build_gateway(registry_obj, user_id="u_ok")
    res = await gw.send("email", "user@example.com", "hello")
    assert res.ok is True, (
        f"Genuine success string must NOT be treated as error; got ok={res.ok!r}"
    )
