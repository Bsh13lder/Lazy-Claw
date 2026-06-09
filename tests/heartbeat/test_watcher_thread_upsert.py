"""Test that _upsert_threads_for_items mirrors new channel items into thread_store.

Task B4: helper upserts one thread per item and returns the most-recently
processed contact handle so check_mcp_watcher can stash it as _latest_contact.

Also covers the single-upsert-point design: items are upserted exactly once
when first detected, before the batch/immediate split, so no double-counting.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from lazyclaw.comms import thread_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.mcp_watcher import (
    _upsert_threads_for_items,
    build_mcp_watcher_context,
    check_mcp_watcher,
)


@pytest.fixture
async def config(tmp_path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo!!")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def user_id():
    return "u1"


@pytest.mark.asyncio
async def test_upsert_threads_for_items(config, user_id):
    # WhatsApp items use "from" as the JID / handle (no "handle" key in real data)
    items = [
        {"id": "m1", "from": "+34600000000", "body": "Hi!"},
        {"id": "m2", "from": "bob@x.com", "body": "Yo"},
    ]
    latest = await _upsert_threads_for_items(config, user_id, "whatsapp", items)
    threads = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(threads) == 2
    # last item processed is m2 — its handle is "bob@x.com"
    assert latest == "bob@x.com"


@pytest.mark.asyncio
async def test_upsert_threads_empty_items_returns_none(config, user_id):
    """Empty items list returns None without writing any threads."""
    latest = await _upsert_threads_for_items(config, user_id, "whatsapp", [])
    assert latest is None
    threads = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(threads) == 0


@pytest.mark.asyncio
async def test_upsert_threads_skips_items_without_handle(config, user_id):
    """Items with no usable contact handle are skipped silently."""
    items = [
        {"id": "m1", "from": "", "body": "no handle"},
        {"id": "m2", "from": "alice@example.com", "body": "has handle"},
    ]
    latest = await _upsert_threads_for_items(config, user_id, "email", items)
    threads = await thread_store.list_threads(config, user_id, channel="email")
    assert len(threads) == 1
    assert latest == "alice@example.com"


@pytest.mark.asyncio
async def test_upsert_threads_increments_unread_on_repeat(config, user_id):
    """Calling twice for the same handle increments unread_count to 2."""
    items = [{"id": "m1", "from": "+34600000000", "body": "first"}]
    await _upsert_threads_for_items(config, user_id, "whatsapp", items)
    items2 = [{"id": "m2", "from": "+34600000000", "body": "second"}]
    await _upsert_threads_for_items(config, user_id, "whatsapp", items2)
    threads = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(threads) == 1
    assert threads[0]["unread_count"] == 2


@pytest.mark.asyncio
async def test_upsert_threads_instagram_uses_user_field(config, user_id):
    """Instagram items have 'user' not 'from' — helper maps via 'user' fallback."""
    items = [
        {"id": "n1", "user": "@pal", "text": "liked your photo", "type": "like"},
    ]
    latest = await _upsert_threads_for_items(config, user_id, "instagram", items)
    threads = await thread_store.list_threads(config, user_id, channel="instagram")
    assert len(threads) == 1
    assert latest == "@pal"


@pytest.mark.asyncio
async def test_batch_path_populates_channel_threads(config, user_id):
    """Items detected on the batch path must populate channel_threads.

    Design: the single-upsert-point fires as soon as new_items are detected
    (before the batch/immediate split), so both batch and immediate paths
    write threads without double-counting unread.

    We call check_mcp_watcher twice with batch_window=300 (5 min):
      Turn 1: one new message arrives — still within window, no notification,
              but channel_threads MUST already have the thread (unread=1).
      Turn 2: same sender sends a second message — still batching, but
              channel_threads now has unread=2 (each item counted once).
    """
    wa_response = json.dumps({
        "messages": [
            {"id": "m1", "from": "34611000001@s.whatsapp.net", "body": "hey", "fromMe": False},
        ]
    })
    mock_client = AsyncMock()
    mock_client.name = "whatsapp"
    mock_client.call_tool = AsyncMock(return_value=wa_response)
    mcp_clients = {"whatsapp": mock_client}

    # Build a context that has already had its baseline poll (last_check > 0,
    # last_seen_ids is empty so "m1" is treated as new) with a batch window.
    ctx = json.loads(build_mcp_watcher_context(
        service="whatsapp",
        tool_name="whatsapp_read",
        tool_args={},
        batch_window=300,
    ))
    ctx["last_check"] = time.time() - 10  # non-zero → skip baseline path

    # Turn 1: m1 is new, batch accumulating (elapsed << 300s)
    changed1, notif1, ctx1 = await check_mcp_watcher(ctx, mcp_clients, config, user_id)

    # Still batching — no notification yet
    assert changed1 is False
    assert notif1 is None

    # But thread MUST already exist with unread=1 (single-upsert-point fired)
    threads_after_t1 = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(threads_after_t1) == 1, "thread should exist even while batching"
    assert threads_after_t1[0]["unread_count"] == 1

    # Turn 2: a second message from the same sender
    wa_response2 = json.dumps({
        "messages": [
            {"id": "m1", "from": "34611000001@s.whatsapp.net", "body": "hey", "fromMe": False},
            {"id": "m2", "from": "34611000001@s.whatsapp.net", "body": "are you there?", "fromMe": False},
        ]
    })
    mock_client.call_tool = AsyncMock(return_value=wa_response2)

    changed2, notif2, ctx2 = await check_mcp_watcher(ctx1, mcp_clients, config, user_id)

    assert changed2 is False
    assert notif2 is None

    # unread must now be 2 — each item counted once, no double-increment
    threads_after_t2 = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(threads_after_t2) == 1
    assert threads_after_t2[0]["unread_count"] == 2
