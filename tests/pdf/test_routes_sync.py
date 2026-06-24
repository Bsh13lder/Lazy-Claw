"""/api/pdf — offline-sync route surface.

Covers /changes (metadata only, no bytes), import-with-client-id (idempotent +
UUID validation), and soft-delete-then-appears-in-changes. Mirrors the fixture
from tests/pdf/test_routes.py.
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
from lazyclaw.gateway.routes.pdf import router as pdf_router
from tests.pdf.conftest import make_text_pdf


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

    import lazyclaw.gateway.routes.pdf as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(pdf_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app)


def _upload(tc, name="doc.pdf", client_id=None):
    pdf = make_text_pdf()
    data = {"id": client_id} if client_id is not None else None
    return tc.post(
        "/api/pdf/import",
        files={"file": (name, pdf, "application/pdf")},
        data=data,
    )


@pytest.mark.asyncio
async def test_changes_endpoint_shape_metadata_only(client):
    tc = client
    pid = _upload(tc).json()["file"]["id"]
    r = tc.get("/api/pdf/changes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"files", "deleted", "now"}
    assert any(f["id"] == pid for f in body["files"])
    assert body["deleted"] == []
    assert body["now"]
    # metadata only — never the bytes
    for f in body["files"]:
        assert "bytes" not in f
        assert "payload" not in f
        assert "pages" in f


@pytest.mark.asyncio
async def test_changes_route_not_shadowed_by_id_capture(client):
    r = client.get("/api/pdf/changes")
    assert r.status_code == 200
    assert "files" in r.json()


@pytest.mark.asyncio
async def test_import_with_client_id(client):
    tc = client
    cid = "11111111-1111-1111-1111-111111111111"
    r = _upload(tc, client_id=cid)
    assert r.status_code == 200, r.text
    assert r.json()["file"]["id"] == cid
    # replay → same id, no duplicate
    r2 = _upload(tc, client_id=cid)
    assert r2.json()["file"]["id"] == cid
    assert tc.get("/api/pdf").json()["count"] == 1


@pytest.mark.asyncio
async def test_import_with_bad_client_id_400(client):
    r = _upload(client, client_id="not-a-uuid")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_soft_delete_then_appears_in_changes(client):
    tc = client
    pid = _upload(tc, name="temp.pdf").json()["file"]["id"]
    since = tc.get("/api/pdf/changes").json()["now"]

    assert tc.delete(f"/api/pdf/{pid}").status_code == 200
    assert tc.get(f"/api/pdf/{pid}").status_code == 404
    assert tc.get("/api/pdf").json()["count"] == 0

    body = tc.get("/api/pdf/changes", params={"since": since}).json()
    assert pid in body["deleted"]
    assert all(f["id"] != pid for f in body["files"])
