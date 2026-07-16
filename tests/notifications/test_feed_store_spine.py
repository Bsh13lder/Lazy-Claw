"""Tests for the Notification-Spine additions to the feed store:
severity, deep_link/actions enrichment, dedup collapse, and read-state.
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


async def test_severity_normalized_and_returned(cfg):
    await feed_store.record_notification(
        cfg, "u1", "info", "t", "b", severity="URGENT",
    )
    await feed_store.record_notification(
        cfg, "u1", "info", "t2", "b2", severity="bogus",
    )
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    sevs = {n["title"]: n["severity"] for n in feed["notifications"]}
    assert sevs["t"] == "urgent"
    assert sevs["t2"] == "normal", "unknown severity falls back to normal"


async def test_deep_link_and_actions_ride_in_meta(cfg):
    link = {"type": "task", "id": "task-1"}
    acts = [{"label": "Done", "action_id": "done:task-1"}]
    await feed_store.record_notification(
        cfg, "u1", "task_reminder", "Buy milk", "due", deep_link=link, actions=acts,
    )
    n = (await feed_store.get_notifications_since(cfg, "u1", None))["notifications"][0]
    assert n["deep_link"] == link
    assert n["actions"] == acts
    assert n["meta"]["deep_link"] == link


async def test_no_metadata_stores_null_meta(cfg):
    # back-compat: nothing provided -> meta stays None
    await feed_store.record_notification(cfg, "u1", "info", "t", "b")
    n = (await feed_store.get_notifications_since(cfg, "u1", None))["notifications"][0]
    assert n["meta"] is None
    assert n["deep_link"] is None
    assert n["actions"] is None


async def test_dedup_collapses_unread_repeat(cfg):
    await feed_store.record_notification(
        cfg, "u1", "watcher_hit", "Upwork", "1 new", dedup_key="w:upwork",
    )
    rec2 = await feed_store.record_notification(
        cfg, "u1", "watcher_hit", "Upwork", "2 new", dedup_key="w:upwork",
    )
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert len(feed["notifications"]) == 1
    assert feed["notifications"][0]["body"] == "2 new"
    assert feed["notifications"][0]["repeat_count"] == 2
    assert rec2["repeat_count"] == 2


async def test_dedup_does_not_collapse_after_read(cfg):
    r1 = await feed_store.record_notification(
        cfg, "u1", "watcher_hit", "Upwork", "1 new", dedup_key="w:upwork",
    )
    await feed_store.mark_read(cfg, "u1", [r1["id"]])
    # a fresh hit after the first was read must create a NEW row
    await feed_store.record_notification(
        cfg, "u1", "watcher_hit", "Upwork", "2 new", dedup_key="w:upwork",
    )
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert len(feed["notifications"]) == 2


async def test_different_dedup_keys_do_not_collapse(cfg):
    await feed_store.record_notification(
        cfg, "u1", "watcher_hit", "A", "x", dedup_key="w:a",
    )
    await feed_store.record_notification(
        cfg, "u1", "watcher_hit", "B", "y", dedup_key="w:b",
    )
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert len(feed["notifications"]) == 2


async def test_mark_read_and_unread_count(cfg):
    r1 = await feed_store.record_notification(cfg, "u1", "info", "a", "1")
    await feed_store.record_notification(cfg, "u1", "info", "b", "2")
    assert await feed_store.get_unread_count(cfg, "u1") == 2

    marked = await feed_store.mark_read(cfg, "u1", [r1["id"]])
    assert marked == 1
    assert await feed_store.get_unread_count(cfg, "u1") == 1

    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert feed["unread"] == 1
    by_id = {n["id"]: n for n in feed["notifications"]}
    assert by_id[r1["id"]]["read_at"] is not None


async def test_mark_all_read(cfg):
    await feed_store.record_notification(cfg, "u1", "info", "a", "1")
    await feed_store.record_notification(cfg, "u1", "info", "b", "2")
    marked = await feed_store.mark_all_read(cfg, "u1")
    assert marked == 2
    assert await feed_store.get_unread_count(cfg, "u1") == 0


async def test_mark_read_scoped_to_user(cfg):
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u2", "bob", "x", "salt-b"),
        )
        await db.commit()
    r = await feed_store.record_notification(cfg, "u1", "info", "a", "1")
    # u2 cannot mark u1's notification read
    marked = await feed_store.mark_read(cfg, "u2", [r["id"]])
    assert marked == 0
    assert await feed_store.get_unread_count(cfg, "u1") == 1


async def test_mark_read_empty_ids_is_noop(cfg):
    assert await feed_store.mark_read(cfg, "u1", []) == 0
