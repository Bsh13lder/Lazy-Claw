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
    assert args["to"] == "+34600000000" and args["message"] == "hello"

@pytest.mark.asyncio
async def test_send_unknown_channel_errors():
    gw = ChannelGateway(mcp_call=AsyncMock())
    res = await gw.send("carrier-pigeon", "x", "y")
    assert res.ok is False and "unsupported" in (res.error or "").lower()

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
