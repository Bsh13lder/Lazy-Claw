"""Tests for the notification HTTP route handlers, invoked directly (no
TestClient — the project's TestClient is known to hang in this suite).

Covers GET/POST /api/settings/notifications + GET /api/notifications?since=.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User
from lazyclaw.gateway.routes.mobile_settings import (
    SetNotificationChannelRequest,
    get_notification_channel_route,
    set_notification_channel_route,
)
from lazyclaw.gateway.routes.notifications import (
    list_notifications,
    read_all_notifications,
    read_notifications,
    unread_count,
)
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


def _user() -> User:
    return User(id="u1", username="alice", display_name=None, encryption_salt="salt-a")


async def test_get_channel_route_default(cfg):
    resp = await get_notification_channel_route(user=_user(), config=cfg)
    assert resp["success"] is True
    assert resp["data"] == {"channel": "telegram"}


async def test_post_channel_route_sets_value(cfg):
    body = SetNotificationChannelRequest(channel="both")
    resp = await set_notification_channel_route(body=body, user=_user(), config=cfg)
    assert resp["success"] is True
    assert resp["data"] == {"channel": "both"}

    # GET reflects the new value.
    got = await get_notification_channel_route(user=_user(), config=cfg)
    assert got["data"]["channel"] == "both"


async def test_post_channel_route_rejects_invalid(cfg):
    body = SetNotificationChannelRequest(channel="nope")
    resp = await set_notification_channel_route(body=body, user=_user(), config=cfg)
    assert resp["success"] is False
    assert "error" in resp


# ── Notification Center read-state routes (Spine, 2026-07-16) ──────────────

async def test_unread_count_route(cfg):
    await feed_store.record_notification(cfg, "u1", "info", "a", "1")
    await feed_store.record_notification(cfg, "u1", "info", "b", "2")
    resp = await unread_count(user=_user(), config=cfg)
    assert resp == {"unread": 2}


async def test_read_route_marks_specific(cfg):
    r = await feed_store.record_notification(cfg, "u1", "info", "a", "1")
    await feed_store.record_notification(cfg, "u1", "info", "b", "2")
    resp = await read_notifications(ids=[r["id"]], user=_user(), config=cfg)
    assert resp == {"marked": 1}
    assert (await unread_count(user=_user(), config=cfg))["unread"] == 1


async def test_read_all_route(cfg):
    await feed_store.record_notification(cfg, "u1", "info", "a", "1")
    await feed_store.record_notification(cfg, "u1", "info", "b", "2")
    resp = await read_all_notifications(user=_user(), config=cfg)
    assert resp == {"marked": 2}
    assert (await unread_count(user=_user(), config=cfg))["unread"] == 0


async def test_feed_route_returns_raw_changes_shape(cfg):
    await feed_store.record_notification(cfg, "u1", "done", "T", "B")
    resp = await list_notifications(since=None, user=_user(), config=cfg)
    # Raw shape (mirrors /changes), NOT wrapped in success/data. The Spine
    # added `unread` (badge count) + enriched per-row fields (2026-07-16).
    assert set(resp.keys()) == {"notifications", "unread", "now"}
    assert resp["unread"] == 1
    assert len(resp["notifications"]) == 1
    item = resp["notifications"][0]
    assert set(item.keys()) == {
        "id", "kind", "title", "body", "meta", "severity", "read_at",
        "created_at", "deep_link", "actions", "repeat_count",
    }


async def test_feed_route_since_filter(cfg):
    await feed_store.record_notification(cfg, "u1", "info", "old", "x")
    cursor = (await list_notifications(since=None, user=_user(), config=cfg))["now"]
    await feed_store.record_notification(cfg, "u1", "info", "new", "y")

    resp = await list_notifications(since=cursor, user=_user(), config=cfg)
    assert [n["title"] for n in resp["notifications"]] == ["new"]
