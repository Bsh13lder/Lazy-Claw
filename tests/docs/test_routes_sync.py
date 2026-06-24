"""/api/docs — offline-sync route surface.

Covers /changes, create-with-client-id (idempotent + UUID validation), and
soft-delete-then-appears-in-changes. Mirrors tests/docs/test_routes.py fixture.
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
from lazyclaw.gateway.routes.docs import router as docs_router


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

    import lazyclaw.gateway.routes.docs as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(docs_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_changes_endpoint_shape(client):
    tc = client
    did = tc.post("/api/docs", json={"name": "A"}).json()["doc"]["id"]
    r = tc.get("/api/docs/changes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"docs", "deleted", "now"}
    assert any(d["id"] == did for d in body["docs"])
    assert body["deleted"] == []
    assert body["now"]


@pytest.mark.asyncio
async def test_changes_route_not_shadowed_by_id_capture(client):
    r = client.get("/api/docs/changes")
    assert r.status_code == 200
    assert "docs" in r.json()


@pytest.mark.asyncio
async def test_create_with_client_id(client):
    tc = client
    cid = "11111111-1111-1111-1111-111111111111"
    r = tc.post("/api/docs", json={"id": cid, "name": "Notes"})
    assert r.status_code == 200, r.text
    assert r.json()["doc"]["id"] == cid
    r2 = tc.post("/api/docs", json={"id": cid, "name": "Notes"})
    assert r2.json()["doc"]["id"] == cid
    assert tc.get("/api/docs").json()["count"] == 1


@pytest.mark.asyncio
async def test_create_with_bad_client_id_400(client):
    r = client.post("/api/docs", json={"id": "not-a-uuid", "name": "X"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_soft_delete_then_appears_in_changes(client):
    tc = client
    did = tc.post("/api/docs", json={"name": "Temp"}).json()["doc"]["id"]
    since = tc.get("/api/docs/changes").json()["now"]

    assert tc.delete(f"/api/docs/{did}").status_code == 200
    assert tc.get(f"/api/docs/{did}").status_code == 404
    assert tc.get("/api/docs").json()["count"] == 0

    body = tc.get("/api/docs/changes", params={"since": since}).json()
    assert did in body["deleted"]
    assert all(d["id"] != did for d in body["docs"])
