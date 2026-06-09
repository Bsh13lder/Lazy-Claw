"""Test that _upsert_threads_for_items mirrors new channel items into thread_store.

Task B4: helper upserts one thread per item and returns the most-recently
processed contact handle so check_mcp_watcher can stash it as _latest_contact.
"""
from __future__ import annotations

import pytest

from lazyclaw.comms import thread_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.mcp_watcher import _upsert_threads_for_items


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
