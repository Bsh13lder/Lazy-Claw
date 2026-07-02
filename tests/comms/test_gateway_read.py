import pytest
from unittest.mock import AsyncMock
from lazyclaw.comms.gateway import ChannelGateway, _parse_messages
from lazyclaw.comms.models import ReadResult

def test_parse_messages_normalizes_shapes():
    raw = {"messages": [
        {"sender": "Alice", "content": "hi", "timestamp": "10:00", "is_mine": False},
        {"from": "me", "body": "yo", "ts": "10:01", "is_mine": True},
    ]}
    msgs = _parse_messages(raw)
    assert msgs[0].sender == "Alice" and msgs[0].text == "hi" and msgs[0].is_mine is False
    assert msgs[1].text == "yo" and msgs[1].is_mine is True


def test_parse_messages_whatsapp_formatmsg_shape():
    """The WhatsApp MCP's _formatMsg emits `time` and `fromMe` (index.js:1171,1202),
    NOT `timestamp`/`is_mine`. If the normalizer doesn't alias them, every
    WhatsApp message renders with an empty timestamp and is_mine=False —
    your own sent replies show up as incoming, with no time. Root cause of
    the "messy and non-accurate" inbox (confirmed by 3 investigator agents)."""
    raw = {"messages": [
        # contact-side message
        {"from": "Maria", "body": "hey", "time": "2026-05-25 20:37:14 UTC",
         "fromMe": False, "id": "M1"},
        # your own reply
        {"from": "me", "body": "on my way", "time": "2026-05-25 20:38:02 UTC",
         "fromMe": True, "id": "M2"},
    ]}
    msgs = _parse_messages(raw)
    assert msgs[0].timestamp == "2026-05-25 20:37:14 UTC"
    assert msgs[0].is_mine is False
    assert msgs[1].timestamp == "2026-05-25 20:38:02 UTC"
    assert msgs[1].is_mine is True, "own reply must be attributed to me, not the contact"

@pytest.mark.asyncio
async def test_read_thread_returns_msgs():
    call = AsyncMock(return_value={"messages": [{"sender": "Bob", "content": "yo", "timestamp": "9:00"}]})
    gw = ChannelGateway(mcp_call=call)
    res = await gw.read_thread("whatsapp", "+1")
    assert res.ok is True
    assert res.error is None
    assert len(res.messages) == 1 and res.messages[0].sender == "Bob"

@pytest.mark.asyncio
async def test_read_thread_unknown_channel_empty_ok():
    """Unknown channel is a caller bug, not a dead channel — stays ok+empty,
    never raises."""
    gw = ChannelGateway(mcp_call=AsyncMock())
    res = await gw.read_thread("carrier-pigeon", "+1")
    assert res.ok is True
    assert res.messages == ()

@pytest.mark.asyncio
async def test_read_thread_genuinely_empty_is_ok():
    """A successful read with zero messages is ok+empty — NOT a failure."""
    call = AsyncMock(return_value={"messages": []})
    gw = ChannelGateway(mcp_call=call)
    res = await gw.read_thread("whatsapp", "+1")
    assert res.ok is True
    assert res.messages == ()
    assert res.error is None

@pytest.mark.asyncio
async def test_read_thread_mcp_exception_is_typed_failure():
    """An MCP exception must surface as ok=False (never raise, never
    masquerade as an empty thread — the silent-empty antipattern)."""
    gw = ChannelGateway(mcp_call=AsyncMock(side_effect=RuntimeError("down")))
    res = await gw.read_thread("whatsapp", "+1")
    assert res.ok is False
    assert res.messages == ()
    assert "down" in (res.error or "")

@pytest.mark.asyncio
async def test_read_thread_error_shaped_result_is_typed_failure():
    """build_gateway._call NEVER raises for an unregistered tool — it returns
    {"status": "error", "error": "unknown tool: ..."} (the container-restart
    case). That shape must also surface as a failure, not an empty thread."""
    call = AsyncMock(return_value={"status": "error", "error": "unknown tool: whatsapp_read"})
    gw = ChannelGateway(mcp_call=call)
    res = await gw.read_thread("whatsapp", "+1")
    assert res.ok is False
    assert "unknown tool" in (res.error or "")

@pytest.mark.asyncio
async def test_read_thread_bare_error_field_is_typed_failure():
    """MCP error shape without a status field (mirrors the send() defense)."""
    call = AsyncMock(return_value={"error": "Contact 'X' not found."})
    gw = ChannelGateway(mcp_call=call)
    res = await gw.read_thread("whatsapp", "+1")
    assert res.ok is False
    assert "not found" in (res.error or "")

@pytest.mark.asyncio
async def test_read_thread_laundered_nonjson_error_is_failure():
    """email_search / instagram_read_dms raise a KeyError when their required
    args (email+query / username) are missing, and the MCP returns a PLAIN-TEXT
    error string. build_gateway._call then launders that into
    {"status":"sent","raw":"Error ..."}. For a READ that's a FAILURE — returning
    ok=True+empty made the inbox render a misleading 'No messages yet' for a
    channel that actually has history (the silent-empty antipattern)."""
    call = AsyncMock(return_value={
        "status": "sent", "raw": "Error in email_search: 'email'",
    })
    gw = ChannelGateway(mcp_call=call)
    res = await gw.read_thread("email", "bob@x.com")
    assert res.ok is False
    assert "email_search" in (res.error or "")


@pytest.mark.asyncio
async def test_read_thread_empty_messages_list_still_ok():
    """Guard for the fix above: a genuinely-empty structured read
    ({"messages": []}) must STAY ok=True — only the laundered non-JSON shape
    (raw + no message-list key) is a failure."""
    call = AsyncMock(return_value={"messages": []})
    gw = ChannelGateway(mcp_call=call)
    res = await gw.read_thread("whatsapp", "+1")
    assert res.ok is True and res.messages == () and res.error is None


def test_parse_messages_alt_list_keys():
    from lazyclaw.comms.gateway import _parse_messages
    assert _parse_messages({"items": [{"sender": "A", "content": "x", "timestamp": "1"}]})[0].sender == "A"
    assert _parse_messages({"emails": [{"from": "B", "body": "y", "date": "2"}]})[0].text == "y"

def test_parse_messages_skips_non_dict_items():
    from lazyclaw.comms.gateway import _parse_messages
    msgs = _parse_messages({"messages": ["garbage", {"sender": "A", "content": "ok", "timestamp": "1"}]})
    assert len(msgs) == 1 and msgs[0].text == "ok"

def test_read_result_is_frozen():
    res = ReadResult(ok=True)
    with pytest.raises(Exception):
        res.ok = False  # type: ignore[misc]
