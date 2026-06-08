"""Tests for the in-app notification feed store (lazyclaw/notifications/feed_store.py).

Covers: record → get_since round-trip (decrypted), encrypted-at-rest, the
``since`` filter, and the ``now``-stamped-before-query delta contract.
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


async def test_record_then_get_since_returns_decrypted(cfg):
    rec = await feed_store.record_notification(
        cfg, "u1", "done", "Task complete", "Searched 12 Upwork jobs."
    )
    assert rec["id"]
    assert rec["kind"] == "done"

    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert "now" in feed
    notifs = feed["notifications"]
    assert len(notifs) == 1
    n = notifs[0]
    assert n["id"] == rec["id"]
    assert n["kind"] == "done"
    assert n["title"] == "Task complete"
    assert n["body"] == "Searched 12 Upwork jobs."
    assert n["created_at"]


async def test_title_and_body_encrypted_at_rest(cfg):
    await feed_store.record_notification(
        cfg, "u1", "push", "Secret title", "Secret body content"
    )
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT kind, title, body FROM notifications WHERE user_id = 'u1'"
        )
        row = await cur.fetchone()
    kind, enc_title, enc_body = row
    assert kind == "push", "kind stays plaintext for filtering"
    assert enc_title.startswith("enc:"), "title must be encrypted at rest"
    assert enc_body.startswith("enc:"), "body must be encrypted at rest"


async def test_since_filters_older_rows(cfg):
    await feed_store.record_notification(cfg, "u1", "info", "first", "body1")
    cursor = (await feed_store.get_notifications_since(cfg, "u1", None))["now"]

    await feed_store.record_notification(cfg, "u1", "info", "second", "body2")

    feed = await feed_store.get_notifications_since(cfg, "u1", cursor)
    titles = [n["title"] for n in feed["notifications"]]
    assert titles == ["second"], "since cursor must drop the older row"


async def test_now_advances_between_reads(cfg):
    a = (await feed_store.get_notifications_since(cfg, "u1", None))["now"]
    b = (await feed_store.get_notifications_since(cfg, "u1", None))["now"]
    assert b >= a, "server now must be monotonic non-decreasing"


async def test_empty_feed_returns_empty_list(cfg):
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert feed["notifications"] == []
    assert feed["now"]


async def test_user_isolation(cfg):
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u2", "bob", "x", "salt-b"),
        )
        await db.commit()
    await feed_store.record_notification(cfg, "u1", "info", "alice-only", "x")

    feed = await feed_store.get_notifications_since(cfg, "u2", None)
    assert feed["notifications"] == [], "u2 must not see u1's notifications"
