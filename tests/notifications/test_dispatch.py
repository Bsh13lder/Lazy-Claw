"""Tests for the unified notification delivery funnel (dispatch.deliver).

Covers:
  - channel_message always records to feed even in telegram-only mode
  - app mode records to feed but does not call _send_telegram_raw
  - both mode records feed AND calls _send_telegram_raw
  - thread_ref is stored in meta
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import dispatch
from lazyclaw.notifications.feed_store import get_notifications_since

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


async def test_channel_message_always_records_even_in_telegram_mode(
    monkeypatch, config, user_id
):
    # Force telegram-only channel; channel_message must STILL hit the feed.
    monkeypatch.setattr(
        dispatch, "get_notification_channel", AsyncMock(return_value="telegram")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(dispatch, "_send_telegram_raw", sent)
    await dispatch.deliver(
        config,
        user_id,
        title="New WhatsApp message",
        body="Hi",
        kind="channel_message",
        thread_ref={"channel": "whatsapp", "contact": "+34600000000"},
    )
    feed = await get_notifications_since(config, user_id, None)
    assert any(n["kind"] == "channel_message" for n in feed["notifications"])
    sent.assert_awaited()  # telegram mode -> also pushed


async def test_app_mode_records_but_no_telegram(monkeypatch, config, user_id):
    monkeypatch.setattr(
        dispatch, "get_notification_channel", AsyncMock(return_value="app")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(dispatch, "_send_telegram_raw", sent)
    await dispatch.deliver(config, user_id, title="t", body="b", kind="info")
    sent.assert_not_awaited()


async def test_both_mode_records_and_sends_telegram(monkeypatch, config, user_id):
    monkeypatch.setattr(
        dispatch, "get_notification_channel", AsyncMock(return_value="both")
    )
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(dispatch, "_send_telegram_raw", sent)
    await dispatch.deliver(config, user_id, title="Heads up", body="work done", kind="done")
    feed = await get_notifications_since(config, user_id, None)
    assert any(n["kind"] == "done" for n in feed["notifications"])
    sent.assert_awaited()


async def test_thread_ref_stored_in_meta(monkeypatch, config, user_id):
    monkeypatch.setattr(
        dispatch, "get_notification_channel", AsyncMock(return_value="app")
    )
    monkeypatch.setattr(dispatch, "_send_telegram_raw", AsyncMock(return_value=True))
    ref = {"channel": "whatsapp", "contact": "+34600000000"}
    await dispatch.deliver(
        config,
        user_id,
        title="msg",
        body="body",
        kind="channel_message",
        thread_ref=ref,
    )
    feed = await get_notifications_since(config, user_id, None)
    matching = [n for n in feed["notifications"] if n["kind"] == "channel_message"]
    assert matching, "channel_message must be recorded"
    assert matching[0]["meta"]["thread_ref"] == ref


async def test_no_thread_ref_stores_no_meta(monkeypatch, config, user_id):
    monkeypatch.setattr(
        dispatch, "get_notification_channel", AsyncMock(return_value="app")
    )
    monkeypatch.setattr(dispatch, "_send_telegram_raw", AsyncMock(return_value=True))
    await dispatch.deliver(config, user_id, title="t", body="b", kind="info")
    feed = await get_notifications_since(config, user_id, None)
    assert feed["notifications"][0]["meta"] is None
