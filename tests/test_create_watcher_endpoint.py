"""POST /api/watchers — create a JS/site watcher from the Web UI.

Covers the "New watcher" button backend, including the use_my_browser
toggle that routes checks through the user's signed-in Brave/CDP by
adding the host to live_hosts (the answer to "can it check a logged-in
link?" — yes, when routed through your browser).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.watchers import router as watchers_router


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(cfg, "u1", "salt-a")

    import lazyclaw.gateway.routes.watchers as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(watchers_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app), cfg


async def _live_hosts(cfg) -> list[str]:
    from lazyclaw.browser.browser_settings import get_live_hosts
    return await get_live_hosts(cfg, "u1")


@pytest.mark.asyncio
async def test_create_public_watcher_no_cdp(client) -> None:
    tc, cfg = client
    r = tc.post("/api/watchers", json={
        "url": "https://example.com/prices",
        "what_to_watch": "price drop",
        "check_interval_minutes": 10,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["id"]
    assert body["host"] == "example.com"
    assert body["live_browser"] is False
    # public page → host NOT added to live_hosts
    assert "example.com" not in await _live_hosts(cfg)


@pytest.mark.asyncio
async def test_create_watcher_use_my_browser_adds_live_host(client) -> None:
    tc, cfg = client
    r = tc.post("/api/watchers", json={
        "url": "https://my-dashboard.internal/app",
        "what_to_watch": "new ticket",
        "use_my_browser": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["live_browser"] is True
    # logged-in page → routed through the user's CDP (added to live_hosts)
    assert "my-dashboard.internal" in await _live_hosts(cfg)


@pytest.mark.asyncio
async def test_create_watcher_builtin_host_always_live(client) -> None:
    tc, cfg = client
    r = tc.post("/api/watchers", json={
        "url": "https://www.upwork.com/messages",
        "what_to_watch": "new client message",
        "use_my_browser": False,
    })
    assert r.status_code == 200, r.text
    # upwork is a builtin live host — live_browser True even without the toggle
    assert r.json()["live_browser"] is True


@pytest.mark.asyncio
async def test_create_watcher_validation(client) -> None:
    tc, _ = client
    assert tc.post("/api/watchers", json={"what_to_watch": "x"}).status_code == 400
    assert tc.post("/api/watchers", json={"url": "https://x.com"}).status_code == 400
    assert tc.post("/api/watchers", json={
        "url": "not-a-url", "what_to_watch": "x",
    }).status_code == 400
