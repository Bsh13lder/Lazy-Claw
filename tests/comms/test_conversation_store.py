"""Tests for lazyclaw.comms.conversation_store — encrypted CRUD + list_due."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.comms import conversation_store as cs
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


@pytest.mark.asyncio
async def test_create_and_get(config, user_id):
    c = await cs.create_conversation(
        config, user_id, channel="whatsapp", contact_handle="+1",
        contact_name="Alice", goal="ask if coming to birthday",
    )
    got = await cs.get_conversation(config, user_id, c["id"])
    assert got["goal"] == "ask if coming to birthday"
    assert got["status"] == "drafting"
    assert got["transcript"] == []
    assert got["user_id"] == user_id   # runner needs this


@pytest.mark.asyncio
async def test_update_status_and_transcript(config, user_id):
    c = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="A", goal="g")
    await cs.update_conversation(config, user_id, c["id"],
        status="running", append_transcript={"dir": "out", "text": "hi", "ts": "10:00"})
    got = await cs.get_conversation(config, user_id, c["id"])
    assert got["status"] == "running"
    assert got["transcript"][-1]["text"] == "hi"


@pytest.mark.asyncio
async def test_list_due(config, user_id):
    c = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="A", goal="g")
    await cs.update_conversation(config, user_id, c["id"],
        status="running", next_poll_at="2000-01-01T00:00:00+00:00")
    due = await cs.list_due(config, "2030-01-01T00:00:00+00:00")
    assert any(t["id"] == c["id"] for t in due)


@pytest.mark.asyncio
async def test_list_due_excludes_future_and_done(config, user_id):
    c1 = await cs.create_conversation(config, user_id, channel="whatsapp", contact_handle="+1", contact_name="A", goal="g")
    await cs.update_conversation(config, user_id, c1["id"], status="running", next_poll_at="2999-01-01T00:00:00+00:00")
    c2 = await cs.create_conversation(config, user_id, channel="whatsapp", contact_handle="+2", contact_name="B", goal="g")
    await cs.update_conversation(config, user_id, c2["id"], status="done", next_poll_at="2000-01-01T00:00:00+00:00")
    due = await cs.list_due(config, "2030-01-01T00:00:00+00:00")
    ids = {t["id"] for t in due}
    assert c1["id"] not in ids and c2["id"] not in ids
