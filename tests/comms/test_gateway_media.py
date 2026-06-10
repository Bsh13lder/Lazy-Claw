"""Media-aware gateway behaviour.

WhatsApp reads now carry an optional ``media`` dict + message ``id`` (set by
mcp-whatsapp's describeMedia). The gateway must surface both on Msg so the
inbox API/mobile app can render a playable voice note / photo instead of a
"[audio]" placeholder, and download_media must dispatch to the per-channel
download tool.
"""
import pytest
from unittest.mock import AsyncMock

from lazyclaw.comms.gateway import ChannelGateway, _parse_messages

pytestmark = pytest.mark.asyncio


def test_parse_messages_surfaces_media_and_id():
    raw = {"messages": [{
        "id": "ABC123",
        "sender": "Maria",
        "content": "[audio]",
        "timestamp": "10:00",
        "media": {
            "kind": "audio", "mimetype": "audio/ogg; codecs=opus",
            "voice_note": True, "seconds": 14,
            "file_name": None, "size_bytes": 9001,
        },
    }]}
    msgs = _parse_messages(raw)
    assert msgs[0].id == "ABC123"
    assert msgs[0].media is not None
    assert msgs[0].media["kind"] == "audio"
    assert msgs[0].media["voice_note"] is True


def test_parse_messages_text_only_has_no_media():
    msgs = _parse_messages({"messages": [
        {"sender": "A", "content": "hi", "timestamp": "1"},
    ]})
    assert msgs[0].media is None
    assert msgs[0].id is None


def test_parse_messages_non_dict_media_dropped():
    msgs = _parse_messages({"messages": [
        {"sender": "A", "content": "x", "timestamp": "1", "media": "weird"},
    ]})
    assert msgs[0].media is None


async def test_download_media_dispatches_whatsapp_tool():
    call = AsyncMock(return_value={
        "path": "/data/whatsapp_sessions/media/ABC123_voice.ogg",
        "kind": "audio", "mimetype": "audio/ogg; codecs=opus",
        "voice_note": True, "size_bytes": 9001,
    })
    gw = ChannelGateway(mcp_call=call)
    result = await gw.download_media("whatsapp", "ABC123", contact="Maria")
    assert result["path"].endswith("voice.ogg")
    call.assert_awaited_once_with(
        "whatsapp_download_media", {"message_id": "ABC123", "contact": "Maria"},
    )


async def test_download_media_unsupported_channel():
    gw = ChannelGateway(mcp_call=AsyncMock())
    result = await gw.download_media("email", "X1")
    assert result.get("error")


async def test_download_media_mcp_error_is_typed_not_raised():
    gw = ChannelGateway(mcp_call=AsyncMock(side_effect=RuntimeError("boom")))
    result = await gw.download_media("whatsapp", "ABC123")
    assert result.get("error")


async def test_download_media_propagates_tool_error_field():
    call = AsyncMock(return_value={"error": "Message 'X' not found in the local cache."})
    gw = ChannelGateway(mcp_call=call)
    result = await gw.download_media("whatsapp", "X")
    assert "not found" in result["error"]
