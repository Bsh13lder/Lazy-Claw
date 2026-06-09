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
from lazyclaw.gateway.routes.notifications import list_notifications
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


async def test_feed_route_returns_raw_changes_shape(cfg):
    await feed_store.record_notification(cfg, "u1", "done", "T", "B")
    resp = await list_notifications(since=None, user=_user(), config=cfg)
    # Raw shape (mirrors /changes), NOT wrapped in success/data.
    assert set(resp.keys()) == {"notifications", "now"}
    assert len(resp["notifications"]) == 1
    item = resp["notifications"][0]
    assert set(item.keys()) == {"id", "kind", "title", "body", "meta", "created_at"}


async def test_feed_route_since_filter(cfg):
    await feed_store.record_notification(cfg, "u1", "info", "old", "x")
    cursor = (await list_notifications(since=None, user=_user(), config=cfg))["now"]
    await feed_store.record_notification(cfg, "u1", "info", "new", "y")

    resp = await list_notifications(since=cursor, user=_user(), config=cfg)
    assert [n["title"] for n in resp["notifications"]] == ["new"]
