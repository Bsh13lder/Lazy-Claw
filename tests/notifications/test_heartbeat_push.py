"""Tests for the preference-aware heartbeat push funnel.

The heartbeat daemon (reminders, nags, pulses, watcher notices, expense
alerts, EOD summaries) fires through a single ``telegram_push`` callback.
``deliver_heartbeat_push`` is the funnel both wiring sites (cli.py /
cli_tui.py) call so the per-user channel toggle (telegram | app | both)
is honoured:

  - telegram (default) -> Telegram send only, no feed record
  - app                -> feed record only, NO Telegram send
  - both               -> feed record AND Telegram send
  - admin unresolvable / routing error -> fail-open to Telegram
  - feed record failure -> Telegram send still attempted (both mode)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import heartbeat_push
from lazyclaw.notifications.feed_store import get_notifications_since
from lazyclaw.notifications.heartbeat_push import deliver_heartbeat_push

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def user_id():
    return "u1"


async def test_telegram_mode_sends_telegram_and_records_feed(monkeypatch, config, user_id):
    # Spine (2026-07-16): the durable feed row is recorded ALWAYS; telegram
    # mode still sends AND now also records (the Notification Center is the
    # source of truth for "what happened").
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="telegram"),
    )
    send = AsyncMock()
    await deliver_heartbeat_push(config, "🔔 Task reminder", telegram_send=send)
    send.assert_awaited_once()
    feed = await get_notifications_since(config, user_id, None)
    assert len(feed["notifications"]) == 1
    assert feed["notifications"][0]["body"] == "🔔 Task reminder"


async def test_app_mode_records_feed_skips_telegram(monkeypatch, config, user_id):
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="app"),
    )
    send = AsyncMock()
    await deliver_heartbeat_push(
        config, "🔔 Buy milk [errands]\n🔊 Reminder #2", telegram_send=send,
    )
    send.assert_not_awaited()
    feed = await get_notifications_since(config, user_id, None)
    assert len(feed["notifications"]) == 1
    entry = feed["notifications"][0]
    assert entry["kind"] == "heartbeat"
    assert entry["title"] == "🔔 Buy milk [errands]"
    assert entry["body"] == "🔔 Buy milk [errands]\n🔊 Reminder #2"


async def test_both_mode_records_feed_and_sends(monkeypatch, config, user_id):
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="both"),
    )
    send = AsyncMock()
    await deliver_heartbeat_push(config, "💸 Recurring expense due", telegram_send=send)
    send.assert_awaited_once()
    feed = await get_notifications_since(config, user_id, None)
    assert len(feed["notifications"]) == 1


async def test_custom_kind_recorded(monkeypatch, config, user_id):
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="app"),
    )
    await deliver_heartbeat_push(
        config, "watcher expired", telegram_send=AsyncMock(), kind="watcher",
    )
    feed = await get_notifications_since(config, user_id, None)
    assert feed["notifications"][0]["kind"] == "watcher"


async def test_unresolvable_admin_fails_open_to_telegram(monkeypatch, config):
    monkeypatch.setattr(
        heartbeat_push, "resolve_admin_user_id", AsyncMock(return_value=None),
    )
    send = AsyncMock()
    await deliver_heartbeat_push(config, "🔔 reminder", telegram_send=send)
    send.assert_awaited_once()


async def test_channel_lookup_error_fails_open_to_telegram(monkeypatch, config):
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    send = AsyncMock()
    await deliver_heartbeat_push(config, "🔔 reminder", telegram_send=send)
    send.assert_awaited_once()


async def test_feed_record_failure_still_sends_telegram(monkeypatch, config):
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="both"),
    )
    monkeypatch.setattr(
        heartbeat_push, "record_notification",
        AsyncMock(side_effect=RuntimeError("encrypt failed")),
    )
    send = AsyncMock()
    await deliver_heartbeat_push(config, "🔔 reminder", telegram_send=send)
    send.assert_awaited_once()


async def test_feed_record_failure_in_app_mode_does_not_raise(monkeypatch, config):
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="app"),
    )
    monkeypatch.setattr(
        heartbeat_push, "record_notification",
        AsyncMock(side_effect=RuntimeError("encrypt failed")),
    )
    send = AsyncMock()
    # must swallow the feed error — heartbeat ticks can never die on a push
    await deliver_heartbeat_push(config, "🔔 reminder", telegram_send=send)
    send.assert_not_awaited()


async def test_telegram_send_error_propagates(monkeypatch, config):
    # the cli wiring catches + logs; the funnel itself must not swallow the
    # send error silently (caller logs it with context)
    monkeypatch.setattr(
        heartbeat_push, "get_notification_channel",
        AsyncMock(return_value="telegram"),
    )
    send = AsyncMock(side_effect=RuntimeError("network down"))
    with pytest.raises(RuntimeError, match="network down"):
        await deliver_heartbeat_push(config, "🔔 reminder", telegram_send=send)
