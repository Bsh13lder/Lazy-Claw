"""Per-contact standing instructions + contact naming (2026-06-10).

Real tmp-DB integration: thread_store instruction CRUD, the heartbeat
handle-hash lookup, and name_thread_contact's three-store fan-out
(thread rename + unified contact store + LazyBrain [[Name]] note).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.comms import contact_naming, thread_store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio

_JID = "34611222333@s.whatsapp.net"


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


async def _seed_thread(config) -> dict:
    return await thread_store.upsert_thread(
        config, "u1",
        channel="whatsapp",
        contact_handle=_JID,
        contact_name="Maria 🌸",
        preview="hola",
    )


# ── instruction CRUD ───────────────────────────────────────────────────────────


async def test_set_and_read_back_instruction(config):
    thread = await _seed_thread(config)
    ok = await thread_store.set_thread_instruction(
        config, "u1", thread["id"], "always answer in Spanish",
    )
    assert ok is True
    fresh = await thread_store.get_thread(config, "u1", thread["id"])
    assert fresh["instruction"] == "always answer in Spanish"


async def test_clear_instruction(config):
    thread = await _seed_thread(config)
    await thread_store.set_thread_instruction(config, "u1", thread["id"], "x")
    await thread_store.set_thread_instruction(config, "u1", thread["id"], None)
    fresh = await thread_store.get_thread(config, "u1", thread["id"])
    assert fresh["instruction"] is None


async def test_set_instruction_unknown_thread_false(config):
    assert await thread_store.set_thread_instruction(
        config, "u1", "nope", "x",
    ) is False


# ── heartbeat lookup by handle ─────────────────────────────────────────────────


async def test_get_instruction_for_handle(config):
    thread = await _seed_thread(config)
    await thread_store.set_thread_instruction(
        config, "u1", thread["id"], "flag anything about invoices",
    )
    found = await thread_store.get_instruction_for_handle(
        config, "u1", "whatsapp", _JID,
    )
    assert found is not None
    assert found["thread_id"] == thread["id"]
    assert found["instruction"] == "flag anything about invoices"
    assert found["contact_name"] == "Maria 🌸"


async def test_get_instruction_for_handle_none_when_unset(config):
    await _seed_thread(config)
    assert await thread_store.get_instruction_for_handle(
        config, "u1", "whatsapp", _JID,
    ) is None


async def test_instruction_survives_watcher_upsert(config):
    # A new inbound message re-upserts the thread — the instruction must stay.
    thread = await _seed_thread(config)
    await thread_store.set_thread_instruction(config, "u1", thread["id"], "keep me")
    await thread_store.upsert_thread(
        config, "u1", channel="whatsapp", contact_handle=_JID,
        preview="new msg", increment_unread=True,
    )
    found = await thread_store.get_instruction_for_handle(
        config, "u1", "whatsapp", _JID,
    )
    assert found and found["instruction"] == "keep me"


# ── contact naming fan-out ─────────────────────────────────────────────────────


async def test_name_thread_contact_updates_all_three_stores(config):
    thread = await _seed_thread(config)
    result = await contact_naming.name_thread_contact(
        config, "u1", thread, "Maria Garcia",
    )
    assert result["thread_renamed"] is True
    assert result["contact_id"]
    assert result["contact_created"] is True
    assert result["note_id"]

    # Thread shows the new display name.
    fresh = await thread_store.get_thread(config, "u1", thread["id"])
    assert fresh["contact_name"] == "Maria Garcia"

    # Unified contact store resolves the person by name AND by phone.
    from lazyclaw.contacts import store as contacts_store
    by_name = await contacts_store.find_contact(config, "u1", "Maria Garcia")
    assert by_name and by_name[0].handles.get("whatsapp_jid") == _JID
    assert "+34611222333" in (by_name[0].handles.get("phone") or [])

    # LazyBrain has the [[Maria Garcia]] page.
    from lazyclaw.lazybrain import store as lb_store
    note = await lb_store.get_note(config, "u1", result["note_id"])
    assert note is not None
    assert note.get("title") == "Maria Garcia"


async def test_renaming_existing_contact_updates_not_duplicates(config):
    thread = await _seed_thread(config)
    first = await contact_naming.name_thread_contact(config, "u1", thread, "Maria")
    second = await contact_naming.name_thread_contact(
        config, "u1", thread, "Maria Garcia",
    )
    # Second naming matched the handle → update, not a new person.
    assert second["contact_created"] is False
    assert second["contact_id"] == first["contact_id"]


async def test_empty_name_rejected(config):
    thread = await _seed_thread(config)
    with pytest.raises(ValueError):
        await contact_naming.name_thread_contact(config, "u1", thread, "   ")
