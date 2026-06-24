"""/api/sheets — offline-sync route surface.

Covers the /changes delta endpoint, create-with-client-id (idempotent + UUID
validation), and soft-delete-then-appears-in-changes. Mirrors the fixture from
tests/sheets/test_routes.py.
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
from lazyclaw.gateway.routes.sheets import router as sheets_router


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

    import lazyclaw.gateway.routes.sheets as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(sheets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_changes_endpoint_shape(client):
    tc = client
    sid = tc.post("/api/sheets", json={"name": "A"}).json()["sheet"]["id"]
    r = tc.get("/api/sheets/changes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"sheets", "deleted", "now"}
    assert any(s["id"] == sid for s in body["sheets"])
    assert body["deleted"] == []
    assert body["now"]


@pytest.mark.asyncio
async def test_changes_route_not_shadowed_by_id_capture(client):
    """`/changes` must resolve to the delta feed, not GET /{sheet_id}=changes."""
    r = client.get("/api/sheets/changes")
    assert r.status_code == 200
    assert "sheets" in r.json()  # not a 404 "Sheet not found"


@pytest.mark.asyncio
async def test_create_with_client_id(client):
    tc = client
    cid = "11111111-1111-1111-1111-111111111111"
    r = tc.post("/api/sheets", json={"id": cid, "name": "Budget"})
    assert r.status_code == 200, r.text
    assert r.json()["sheet"]["id"] == cid
    # replay → same id, no duplicate
    r2 = tc.post("/api/sheets", json={"id": cid, "name": "Budget"})
    assert r2.json()["sheet"]["id"] == cid
    assert tc.get("/api/sheets").json()["count"] == 1


@pytest.mark.asyncio
async def test_create_with_bad_client_id_400(client):
    r = client.post("/api/sheets", json={"id": "not-a-uuid", "name": "X"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_soft_delete_then_appears_in_changes(client):
    tc = client
    sid = tc.post("/api/sheets", json={"name": "Temp"}).json()["sheet"]["id"]
    since = tc.get("/api/sheets/changes").json()["now"]

    assert tc.delete(f"/api/sheets/{sid}").status_code == 200
    # gone from list/get
    assert tc.get(f"/api/sheets/{sid}").status_code == 404
    assert tc.get("/api/sheets").json()["count"] == 0

    body = tc.get("/api/sheets/changes", params={"since": since}).json()
    assert sid in body["deleted"]
    assert all(s["id"] != sid for s in body["sheets"])
