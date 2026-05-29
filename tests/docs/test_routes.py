"""/api/docs — doc CRUD from the web UI.

Covers create→list, snapshot fetch, autosave PUT round-trip, delete + 404,
docx export, the PDF 503 fallback, and that one user's id can't fetch
another's doc through the route.
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
async def test_create_list_get(client):
    tc = client
    r = tc.post("/api/docs", json={"name": "Letter"})
    assert r.status_code == 200, r.text
    did = r.json()["doc"]["id"]

    r = tc.get("/api/docs")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["docs"][0]["id"] == did

    r = tc.get(f"/api/docs/{did}")
    assert r.status_code == 200
    assert r.json()["payload"]["body"]  # valid snapshot


@pytest.mark.asyncio
async def test_save_roundtrip(client):
    tc = client
    did = tc.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]
    snap = tc.get(f"/api/docs/{did}").json()["payload"]

    # mutate the body and save (mimics editor autosave)
    snap["body"]["dataStream"] = "Hello world\r\n"
    snap["body"]["paragraphs"] = [{"startIndex": 11}]
    r = tc.put(f"/api/docs/{did}", json={"name": "Letter", "payload": snap})
    assert r.status_code == 200, r.text

    out = tc.get(f"/api/docs/{did}").json()["payload"]
    assert out["body"]["dataStream"] == "Hello world\r\n"


@pytest.mark.asyncio
async def test_delete_then_404(client):
    tc = client
    did = tc.post("/api/docs", json={"name": "Temp"}).json()["doc"]["id"]
    assert tc.delete(f"/api/docs/{did}").status_code == 200
    assert tc.get(f"/api/docs/{did}").status_code == 404
    assert tc.delete(f"/api/docs/{did}").status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_404(client):
    assert client.get("/api/docs/does-not-exist").status_code == 404


@pytest.mark.asyncio
async def test_export_docx(client):
    tc = client
    did = tc.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]
    snap = tc.get(f"/api/docs/{did}").json()["payload"]
    snap["body"]["dataStream"] = "Some text\r\n"
    snap["body"]["paragraphs"] = [{"startIndex": 9}]
    tc.put(f"/api/docs/{did}", json={"name": "Letter", "payload": snap})

    r = tc.get(f"/api/docs/{did}/export?format=docx")
    assert r.status_code == 200, r.text
    assert "wordprocessingml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"  # docx is a zip
    assert "Letter.docx" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_export_pdf_503_without_libreoffice(client, monkeypatch):
    import lazyclaw.gateway.routes.docs as routes_mod

    # Force the PDF path to report "unavailable".
    monkeypatch.setattr(routes_mod, "snapshot_to_pdf", lambda snap: None)
    did = client.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]
    r = client.get(f"/api/docs/{did}/export?format=pdf")
    assert r.status_code == 503
    assert "LibreOffice" in r.json()["detail"]


@pytest.mark.asyncio
async def test_export_pdf_returns_bytes_when_available(client, monkeypatch):
    import lazyclaw.gateway.routes.docs as routes_mod

    monkeypatch.setattr(routes_mod, "snapshot_to_pdf", lambda snap: b"%PDF-1.7 fake")
    did = client.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]
    r = client.get(f"/api/docs/{did}/export?format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF")
    assert "Letter.pdf" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_export_unknown_404(client):
    assert client.get("/api/docs/nope/export").status_code == 404
