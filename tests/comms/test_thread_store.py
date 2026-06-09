"""Tests for lazyclaw.comms.thread_store — encrypted CRUD + changes feed."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.comms import thread_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
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


async def test_upsert_creates_then_bumps(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", preview="hello", last_seen_msg_id="m1", increment_unread=True,
    )
    assert t["unread_count"] == 1 and t["contact_name"] == "Alice"
    t2 = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", preview="second", last_seen_msg_id="m2", increment_unread=True,
    )
    assert t2["id"] == t["id"] and t2["unread_count"] == 2 and t2["last_preview"] == "second"


async def test_mark_read_zeroes_unread(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="email", contact_handle="bob@x.com",
        contact_name="Bob", preview="hi", last_seen_msg_id="e1", increment_unread=True,
    )
    await thread_store.mark_thread_read(config, user_id, t["id"])
    got = await thread_store.get_thread(config, user_id, t["id"])
    assert got["unread_count"] == 0


async def test_changes_includes_deletes(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+1", contact_name="C",
        preview="p", last_seen_msg_id="m1", increment_unread=False,
    )
    snap = await thread_store.get_thread_changes(config, user_id, None)
    since = snap["now"]
    await thread_store.delete_thread(config, user_id, t["id"])
    delta = await thread_store.get_thread_changes(config, user_id, since)
    assert t["id"] in delta["deleted"]


async def test_list_threads_filters_by_channel(config, user_id):
    await thread_store.upsert_thread(config, user_id, channel="whatsapp", contact_handle="+1", contact_name="A", preview="p", last_seen_msg_id="m1")
    await thread_store.upsert_thread(config, user_id, channel="email", contact_handle="a@b.c", contact_name="B", preview="q", last_seen_msg_id="e1")
    wa = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(wa) == 1 and wa[0]["channel"] == "whatsapp"
    allt = await thread_store.list_threads(config, user_id)
    assert len(allt) == 2
