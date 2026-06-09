"""Tests for ChannelGateway.read_thread — injected mcp_call path.

These test the _DISPATCH table dispatch, not registry resolution (that is
covered by test_gateway_build.py). mcp_call is injected directly.

Arg shapes verified against real MCP server inputSchemas:
  whatsapp_read:     contact (optional) + limit (optional)
  email_search:      email (account, defaults "") + query + contact + limit
                     (contact is passed as dynamic arg; extra_read adds email="")
  instagram_read_dms: username (account, defaults "") + unread_only=False
                      + contact + limit (passed dynamically)
"""
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
async def test_read_thread_whatsapp_returns_msgs():
    call = AsyncMock(return_value={"messages": [{"sender": "Bob", "content": "yo", "timestamp": "9:00"}]})
    gw = ChannelGateway(mcp_call=call)
    msgs = await gw.read_thread("whatsapp", "+1")
    assert len(msgs) == 1 and msgs[0].sender == "Bob"
    name, args = call.await_args.args
    assert name == "whatsapp_read"
    assert args["contact"] == "+1"
    assert args["limit"] == 30
    # WhatsApp read has no extra args
    assert set(args.keys()) == {"contact", "limit"}


@pytest.mark.asyncio
async def test_read_thread_email_passes_extra_args():
    """email_search call must include email (account) from extra_read_args."""
    call = AsyncMock(return_value={"emails": [{"from": "x@y.com", "body": "hello", "date": "now"}]})
    gw = ChannelGateway(mcp_call=call)
    msgs = await gw.read_thread("email", "x@y.com")
    assert len(msgs) == 1
    name, args = call.await_args.args
    assert name == "email_search"
    # extra_read injects email=""
    assert "email" in args


@pytest.mark.asyncio
async def test_read_thread_instagram_passes_extra_args():
    """instagram_read_dms call must include username (account) and unread_only=False."""
    call = AsyncMock(return_value={"messages": [{"sender": "ig_user", "content": "hey", "timestamp": "now"}]})
    gw = ChannelGateway(mcp_call=call)
    msgs = await gw.read_thread("instagram", "ig_user")
    name, args = call.await_args.args
    assert name == "instagram_read_dms"
    assert "username" in args
    assert args.get("unread_only") is False


@pytest.mark.asyncio
async def test_read_thread_unknown_channel_empty():
    gw = ChannelGateway(mcp_call=AsyncMock())
    assert await gw.read_thread("carrier-pigeon", "+1") == []


@pytest.mark.asyncio
async def test_read_thread_swallows_errors():
    gw = ChannelGateway(mcp_call=AsyncMock(side_effect=RuntimeError("down")))
    assert await gw.read_thread("whatsapp", "+1") == []


def test_parse_messages_alt_list_keys():
    from lazyclaw.comms.gateway import _parse_messages
    assert _parse_messages({"items": [{"sender": "A", "content": "x", "timestamp": "1"}]})[0].sender == "A"
    assert _parse_messages({"emails": [{"from": "B", "body": "y", "date": "2"}]})[0].text == "y"


def test_parse_messages_skips_non_dict_items():
    from lazyclaw.comms.gateway import _parse_messages
    msgs = _parse_messages({"messages": ["garbage", {"sender": "A", "content": "ok", "timestamp": "1"}]})
    assert len(msgs) == 1 and msgs[0].text == "ok"
