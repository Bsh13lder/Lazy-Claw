"""BUG 6 — `_extract_new_items` must carry the STABLE `chat_jid` forward.

The WhatsApp MCP emits `chat_jid` (msg.key.remoteJid, e.g.
``34611222333@s.whatsapp.net``) as the stable chat identifier and `from`
= pushName (a mutable DISPLAY NAME). `_extract_new_items` used to drop
`chat_jid`, so the downstream `_upsert_threads_for_items` — which prefers
`chat_jid` — never saw it and fell back to the pushName. When the pushName
changed (emoji / nickname edit) the HMAC dedup key changed too → a NEW
duplicate thread row, and opening it read `whatsapp_read(contact="Maria 🌸")`
which never resolved → empty-on-open.

These tests drive the REAL pipeline (extract → upsert) end-to-end.
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
    _extract_new_items,
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


def _wa_msg(msg_id: str, push: str, jid: str, body: str) -> dict:
    """A realistic WhatsApp MCP message: `from` = pushName, `chat_jid` = JID."""
    return {
        "id": msg_id,
        "from": push,
        "body": body,
        "time": "2026-07-01 10:00:00 UTC",
        "fromMe": False,
        "type": "direct",
        "chatName": push,
        "chat_jid": jid,
        "muted": False,
    }


def test_extract_carries_chat_jid():
    """The extracted item MUST retain `chat_jid` (was silently dropped)."""
    data = {"messages": [_wa_msg("m1", "Maria 🌸", "34611222333@s.whatsapp.net", "hola")]}
    items = _extract_new_items(data, set(), "whatsapp")
    assert len(items) == 1
    assert items[0]["chat_jid"] == "34611222333@s.whatsapp.net"
    # `from` stays the display name for the notification / contact_name.
    assert items[0]["from"] == "Maria 🌸"


async def _poll(config, mcp_clients, ctx):
    return await check_mcp_watcher(ctx, mcp_clients, config, "u1")


@pytest.mark.asyncio
async def test_changed_pushname_same_jid_is_one_thread(config):
    """Changed pushName + SAME chat_jid → ONE thread keyed on the JID."""
    mock_client = AsyncMock()
    mock_client.name = "whatsapp"
    mcp_clients = {"whatsapp": mock_client}

    ctx = json.loads(build_mcp_watcher_context(
        service="whatsapp", tool_name="whatsapp_read", tool_args={},
    ))
    ctx["last_check"] = time.time() - 10  # skip the silent first-poll baseline

    # Poll 1 — pushName "Maria"
    mock_client.call_tool = AsyncMock(return_value=json.dumps({
        "messages": [_wa_msg("m1", "Maria", "34611222333@s.whatsapp.net", "hi")],
    }))
    _, _, ctx = await _poll(config, mcp_clients, ctx)

    # Poll 2 — SAME JID, pushName now "Maria 🌸" (emoji edit), new message id
    mock_client.call_tool = AsyncMock(return_value=json.dumps({
        "messages": [_wa_msg("m2", "Maria 🌸", "34611222333@s.whatsapp.net", "you there?")],
    }))
    await _poll(config, mcp_clients, ctx)

    threads = await thread_store.list_threads(config, "u1", channel="whatsapp")
    assert len(threads) == 1, "pushName change must NOT fork the thread"
    assert threads[0]["contact_handle"] == "34611222333@s.whatsapp.net"
    # contact_name tracks the latest human display name for the UI title.
    assert threads[0]["contact_name"] == "Maria 🌸"
