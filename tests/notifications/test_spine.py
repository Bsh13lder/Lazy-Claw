"""Tests for the Notification Spine (lazyclaw/notifications/spine.py).

The spine is the one canonical emit API. Core contract:
  - ALWAYS records a durable feed row, even in telegram-only mode
    (the Notification Center is the source of truth for "what happened");
  - the telegram/app/both toggle controls Telegram loudness only;
  - silent records durably but suppresses the Telegram send;
  - structured actions derive a Telegram inline keyboard;
  - dedup_key collapses a recent unread repeat.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import spine
from lazyclaw.notifications.feed_store import get_notifications_since

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
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


async def _feed(cfg):
    return (await get_notifications_since(cfg, "u1", None))["notifications"]


async def test_always_records_feed_even_in_telegram_mode(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="telegram")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(spine, "_send_telegram_raw", sent)

    n = await spine.notify(
        cfg, "u1", kind="task_reminder", title="Buy milk", body="due now",
    )
    feed = await _feed(cfg)
    assert len(feed) == 1, "durable feed row must exist regardless of toggle"
    assert feed[0]["kind"] == "task_reminder"
    assert n.telegram_sent is True
    sent.assert_awaited()  # telegram channel -> loud


async def test_app_mode_records_but_no_telegram(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="app")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(spine, "_send_telegram_raw", sent)

    n = await spine.notify(cfg, "u1", kind="watcher_hit", title="t", body="b")
    assert len(await _feed(cfg)) == 1
    sent.assert_not_awaited()
    assert n.telegram_sent is False


async def test_silent_records_but_suppresses_telegram(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="both")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(spine, "_send_telegram_raw", sent)

    await spine.notify(cfg, "u1", kind="info", title="t", body="b", silent=True)
    assert len(await _feed(cfg)) == 1, "silent still records durably"
    sent.assert_not_awaited()


async def test_actions_become_inline_keyboard(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="telegram")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(spine, "_send_telegram_raw", sent)

    await spine.notify(
        cfg, "u1", kind="approval_needed", title="Approve?", body="pay $10",
        actions=[
            {"label": "✅ Approve", "action_id": "approve:42"},
            {"label": "⏭ Skip", "action_id": "skip:42"},
        ],
    )
    kwargs = sent.await_args.kwargs
    kb = kwargs["inline_keyboard"]
    assert kb == [
        [{"text": "✅ Approve", "callback_data": "approve:42"}],
        [{"text": "⏭ Skip", "callback_data": "skip:42"}],
    ]


async def test_deep_link_persisted_and_returned(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="app")
    )
    monkeypatch.setattr(spine, "_send_telegram_raw", AsyncMock(return_value=True))

    link = {"type": "thread", "id": "t-9", "channel": "whatsapp"}
    n = await spine.notify(
        cfg, "u1", kind="channel_message", title="msg", body="hi",
        deep_link=link,
    )
    assert n.deep_link == link
    feed = await _feed(cfg)
    assert feed[0]["deep_link"] == link


async def test_dedup_collapses_recent_unread_repeat(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="app")
    )
    monkeypatch.setattr(spine, "_send_telegram_raw", AsyncMock(return_value=True))

    await spine.notify(
        cfg, "u1", kind="watcher_hit", title="Upwork", body="1 new",
        dedup_key="watcher:upwork-inbox",
    )
    n2 = await spine.notify(
        cfg, "u1", kind="watcher_hit", title="Upwork", body="2 new",
        dedup_key="watcher:upwork-inbox",
    )
    feed = await _feed(cfg)
    assert len(feed) == 1, "same dedup_key collapses into one row"
    assert feed[0]["body"] == "2 new", "collapsed row shows the latest content"
    assert n2.repeat_count == 2


async def test_severity_recorded(monkeypatch, cfg):
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="app")
    )
    monkeypatch.setattr(spine, "_send_telegram_raw", AsyncMock(return_value=True))
    await spine.notify(
        cfg, "u1", kind="approval_needed", title="t", body="b", severity="urgent",
    )
    feed = await _feed(cfg)
    assert feed[0]["severity"] == "urgent"


async def test_feed_record_failure_does_not_crash_producer(monkeypatch, cfg):
    # A durable-record failure must never propagate into the producer.
    monkeypatch.setattr(
        spine, "record_notification", AsyncMock(side_effect=RuntimeError("db down"))
    )
    monkeypatch.setattr(
        spine, "get_notification_channel", AsyncMock(return_value="app")
    )
    monkeypatch.setattr(spine, "_send_telegram_raw", AsyncMock(return_value=True))
    n = await spine.notify(cfg, "u1", kind="info", title="t", body="b")
    assert n.kind == "info"  # returns a best-effort object, does not raise
