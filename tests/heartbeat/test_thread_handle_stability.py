"""Thread handles must be STABLE identifiers, never display names.

WhatsApp items carry `from` = pushName (display name) and `chat_jid` = the
real chat JID. Storing the display name as contact_handle broke the whole
inbox: later reads (`whatsapp_read(contact=<handle>)`) and sends failed to
resolve "Maria 🌸" while the JID always resolves. The watcher upsert must
prefer `chat_jid` and keep the human name in contact_name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.comms import thread_store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.mcp_watcher import _upsert_threads_for_items

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
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


async def test_whatsapp_handle_prefers_chat_jid(config):
    latest = await _upsert_threads_for_items(config, "u1", "whatsapp", [{
        "from": "Maria 🌸",
        "pushName": "Maria 🌸",
        "chat_jid": "34611222333@s.whatsapp.net",
        "body": "hola!",
    }])
    assert latest == "34611222333@s.whatsapp.net"
    threads = await thread_store.list_threads(config, "u1")
    assert len(threads) == 1
    assert threads[0]["contact_handle"] == "34611222333@s.whatsapp.net"
    assert threads[0]["contact_name"] == "Maria 🌸"


async def test_fallback_to_from_when_no_jid(config):
    # Email/instagram items (and legacy whatsapp payloads) have no chat_jid —
    # their `from`/`user` IS the stable handle.
    await _upsert_threads_for_items(config, "u1", "email", [{
        "from": "bob@example.com",
        "subject": "invoice",
    }])
    threads = await thread_store.list_threads(config, "u1")
    assert threads[0]["contact_handle"] == "bob@example.com"


async def test_same_jid_different_pushname_is_one_thread(config):
    # pushName changes (emoji edits, renames) must NOT fork the thread.
    for push in ("Maria", "Maria 🌸"):
        await _upsert_threads_for_items(config, "u1", "whatsapp", [{
            "from": push,
            "pushName": push,
            "chat_jid": "34611222333@s.whatsapp.net",
            "body": "hey",
        }])
    threads = await thread_store.list_threads(config, "u1")
    assert len(threads) == 1
