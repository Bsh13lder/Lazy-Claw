import pytest
from unittest.mock import AsyncMock
from lazyclaw.comms.gateway import ChannelGateway, _parse_messages

def test_parse_messages_normalizes_shapes():
    raw = {"messages": [
        {"sender": "Alice", "content": "hi", "timestamp": "10:00", "is_mine": False},
        {"from": "me", "body": "yo", "ts": "10:01", "is_mine": True},
    ]}
    msgs = _parse_messages(raw)
    assert msgs[0].sender == "Alice" and msgs[0].text == "hi" and msgs[0].is_mine is False
    assert msgs[1].text == "yo" and msgs[1].is_mine is True

@pytest.mark.asyncio
async def test_read_thread_returns_msgs():
    call = AsyncMock(return_value={"messages": [{"sender": "Bob", "content": "yo", "timestamp": "9:00"}]})
    gw = ChannelGateway(mcp_call=call)
    msgs = await gw.read_thread("whatsapp", "+1")
    assert len(msgs) == 1 and msgs[0].sender == "Bob"

@pytest.mark.asyncio
async def test_read_thread_unknown_channel_empty():
    gw = ChannelGateway(mcp_call=AsyncMock())
    assert await gw.read_thread("carrier-pigeon", "+1") == []

@pytest.mark.asyncio
async def test_read_thread_swallows_errors():
    gw = ChannelGateway(mcp_call=AsyncMock(side_effect=RuntimeError("down")))
    assert await gw.read_thread("whatsapp", "+1") == []
