"""Tests for the encrypted ``meta`` sidecar on notification feed entries.

Covers: round-trip (record → get_since) with a ``thread_ref`` payload, and
the default-None behaviour when no meta is provided.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import feed_store

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


async def test_record_and_read_meta(cfg):
    rec = await feed_store.record_notification(
        cfg,
        "u1",
        "channel_message",
        "New WhatsApp message",
        "Hi there",
        meta={"thread_ref": {"channel": "whatsapp", "contact": "+34600000000"}},
    )
    assert rec["meta"]["thread_ref"]["channel"] == "whatsapp"

    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    got = [n for n in feed["notifications"] if n["id"] == rec["id"]][0]
    assert got["meta"]["thread_ref"]["contact"] == "+34600000000"


async def test_meta_defaults_none(cfg):
    rec = await feed_store.record_notification(cfg, "u1", "info", "t", "b")
    assert rec["meta"] is None

    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert feed["notifications"][0]["meta"] is None


async def test_meta_encrypted_at_rest(cfg):
    """meta must be stored as an enc:v1 blob, not plaintext JSON."""
    user_id = "u1"
    await feed_store.record_notification(
        cfg,
        user_id,
        "channel_message",
        "title",
        "body",
        meta={"thread_ref": {"channel": "email", "contact": "alice@example.com"}},
    )
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT meta FROM notifications WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0].startswith("enc:"), "meta must be AES-encrypted at rest"


async def test_corrupt_meta_returns_none_not_raise(cfg):
    """get_notifications_since must not raise when stored meta is corrupt/undecodable."""
    user_id = "u1"
    rec = await feed_store.record_notification(
        cfg,
        user_id,
        "channel_message",
        "title",
        "body",
        meta={"thread_ref": {"channel": "telegram"}},
    )

    # Corrupt the stored meta blob with an invalid enc:v1 value.
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE notifications SET meta = ? WHERE id = ?",
            ("enc:v1:AAAA:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=", rec["id"]),
        )
        await db.commit()

    # Must not raise; the item should come back with meta=None.
    feed = await feed_store.get_notifications_since(cfg, user_id, None)
    matching = [n for n in feed["notifications"] if n["id"] == rec["id"]]
    assert len(matching) == 1
    assert matching[0]["meta"] is None
