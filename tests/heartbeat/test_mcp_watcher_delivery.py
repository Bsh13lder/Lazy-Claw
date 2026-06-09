"""Test that MCP-watcher channel-message alerts route through deliver()
so they reach the in-app feed (Flutter) as well as Telegram.

Task A4: rewire the push_telegram call in _check_mcp_watchers to use
_notify_channel_message which delegates to notifications.dispatch.deliver.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.config import Config
from lazyclaw.heartbeat.daemon import HeartbeatDaemon


@pytest.fixture
def config(tmp_path):
    return Config(database_dir=tmp_path, server_secret="test-server-secret")


@pytest.mark.asyncio
async def test_mcp_watcher_routes_through_deliver(config):
    user_id = "test-user-mcp-watcher"
    daemon = HeartbeatDaemon(config=config, lane_queue=None)
    with patch("lazyclaw.heartbeat.daemon.deliver", new=AsyncMock()) as deliver_mock:
        await daemon._notify_channel_message(
            user_id,
            service="whatsapp",
            notification="🔔 New message from Alice",
            contact="+34600000000",
            inline_keyboard=[[{"text": "Mute", "callback_data": "m"}]],
        )
        deliver_mock.assert_awaited_once()
        kwargs = deliver_mock.await_args.kwargs
        assert kwargs["kind"] == "channel_message"
        assert kwargs["thread_ref"] == {
            "channel": "whatsapp",
            "contact": "+34600000000",
        }


@pytest.mark.asyncio
async def test_mcp_watcher_no_contact_passes_none_thread_ref(config):
    """When contact is not provided, thread_ref should be None."""
    user_id = "test-user-mcp-watcher-2"
    daemon = HeartbeatDaemon(config=config, lane_queue=None)
    with patch("lazyclaw.heartbeat.daemon.deliver", new=AsyncMock()) as deliver_mock:
        await daemon._notify_channel_message(
            user_id,
            service="email",
            notification="🔔 New email",
        )
        deliver_mock.assert_awaited_once()
        kwargs = deliver_mock.await_args.kwargs
        assert kwargs["kind"] == "channel_message"
        assert kwargs["thread_ref"] is None


@pytest.mark.asyncio
async def test_mcp_watcher_title_strips_bell_prefix(config):
    """title should strip the leading 🔔 bell and whitespace."""
    user_id = "test-user-mcp-watcher-3"
    daemon = HeartbeatDaemon(config=config, lane_queue=None)
    with patch("lazyclaw.heartbeat.daemon.deliver", new=AsyncMock()) as deliver_mock:
        await daemon._notify_channel_message(
            user_id,
            service="instagram",
            notification="🔔 New DM from @friend",
        )
        deliver_mock.assert_awaited_once()
        kwargs = deliver_mock.await_args.kwargs
        # title is the stripped version (lstrip + strip), body is the full notification
        assert kwargs["body"] == "🔔 New DM from @friend"
        assert "🔔" not in kwargs["title"]
