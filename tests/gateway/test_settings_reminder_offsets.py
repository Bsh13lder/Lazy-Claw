"""``PATCH/GET /api/settings/general`` must accept + persist reminder_offsets.

Pinned bug: ``UpdateGeneralRequest`` only whitelisted search_provider /
show_cost_badges / agent_mode, so Pydantic silently DROPPED any
``reminder_offsets`` the mobile/web client sent — the PATCH became a no-op.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config, load_config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.system import router as system_router


async def _setup_db(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    return cfg


def _fake_user() -> User:
    return User(id="u1", username="alice", display_name=None,
                encryption_salt="salt-a", role="user")


@pytest.fixture
async def client(tmp_path: Path):
    cfg = await _setup_db(tmp_path)
    app = FastAPI()
    app.include_router(system_router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[load_config] = lambda: cfg
    try:
        yield TestClient(app)
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_get_returns_default_reminder_offsets(client) -> None:
    r = client.get("/api/settings/general")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["reminder_offsets"] == ["-2h", "-1h"]


@pytest.mark.asyncio
async def test_patch_reminder_offsets_accepted(client) -> None:
    r = client.patch(
        "/api/settings/general",
        json={"reminder_offsets": ["-1d", "-2h", "-30m"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True, body
    assert body["data"]["reminder_offsets"] == ["-1d", "-2h", "-30m"]


@pytest.mark.asyncio
async def test_patch_reminder_offsets_persists_across_get(client) -> None:
    client.patch("/api/settings/general", json={"reminder_offsets": ["-45m"]})
    r = client.get("/api/settings/general")
    assert r.json()["data"]["reminder_offsets"] == ["-45m"]


@pytest.mark.asyncio
async def test_patch_invalid_offset_rejected(client) -> None:
    r = client.patch(
        "/api/settings/general",
        json={"reminder_offsets": ["banana"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "banana" in body["error"] or "offset" in body["error"].lower()


@pytest.mark.asyncio
async def test_patch_reminder_offsets_does_not_clobber_other_fields(client) -> None:
    """Setting offsets leaves search_provider at its default."""
    client.patch("/api/settings/general", json={"reminder_offsets": ["-1h"]})
    r = client.get("/api/settings/general")
    data = r.json()["data"]
    assert data["reminder_offsets"] == ["-1h"]
    assert data["search_provider"] == "auto"
