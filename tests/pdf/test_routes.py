"""/api/pdf — PDF upload / view / manage from the web UI.

Covers upload→list→meta→raw(200, %PDF)→extract→download→delete→404, plus
non-PDF and empty-file rejection (400), and cross-user isolation.
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


def _upload(tc, name="doc.pdf"):
    pdf = make_text_pdf()
    r = tc.post(
        "/api/pdf/import",
        files={"file": (name, pdf, "application/pdf")},
    )
    return r


@pytest.mark.asyncio
async def test_upload_list_meta(client):
    tc = client
    r = _upload(tc)
    assert r.status_code == 200, r.text
    meta = r.json()["file"]
    pid = meta["id"]
    assert meta["pages"] == 1

    r = tc.get("/api/pdf")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["files"][0]["id"] == pid

    r = tc.get(f"/api/pdf/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pid
    assert "bytes" not in body  # meta only


@pytest.mark.asyncio
async def test_raw_returns_pdf(client):
    tc = client
    pid = _upload(tc).json()["file"]["id"]
    r = tc.get(f"/api/pdf/{pid}/raw")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    assert "inline" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_extract(client):
    tc = client
    pid = _upload(tc).json()["file"]["id"]
    r = tc.get(f"/api/pdf/{pid}/extract")
    assert r.status_code == 200
    assert r.json()["pages"] == 1
    assert "Hello world" in r.json()["text"]


@pytest.mark.asyncio
async def test_download_attachment(client):
    tc = client
    pid = _upload(tc, name="report.pdf").json()["file"]["id"]
    r = tc.get(f"/api/pdf/{pid}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and "report.pdf" in cd


@pytest.mark.asyncio
async def test_delete_then_404(client):
    tc = client
    pid = _upload(tc).json()["file"]["id"]
    assert tc.delete(f"/api/pdf/{pid}").status_code == 200
    assert tc.get(f"/api/pdf/{pid}").status_code == 404
    assert tc.delete(f"/api/pdf/{pid}").status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_404(client):
    assert client.get("/api/pdf/does-not-exist").status_code == 404
    assert client.get("/api/pdf/does-not-exist/raw").status_code == 404
    assert client.get("/api/pdf/does-not-exist/extract").status_code == 404


@pytest.mark.asyncio
async def test_reject_non_pdf_400(client):
    r = client.post(
        "/api/pdf/import",
        files={"file": ("evil.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_reject_empty_400(client):
    r = client.post(
        "/api/pdf/import",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_route_precedes_capture(client):
    """The literal /import path must win over /{pdf_id}."""
    tc = client
    r = _upload(tc)
    # If /import were captured as {pdf_id}='import', we'd never get a 200 here.
    assert r.status_code == 200
