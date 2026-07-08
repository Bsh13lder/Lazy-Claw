"""GET /versions + POST /versions/{id}/restore — PDF pre-edit recovery routes.

An in-place ✨ edit stashes the prior bytes as a hidden version; these routes
list them and restore one back into the live file.
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
from lazyclaw.pdf import ops
from lazyclaw.pdf.store import archive_and_replace, save_pdf


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
    row = await save_pdf(cfg, "u1", "doc.pdf", ops.generate_from_text("Original.", title="T"))
    # One in-place edit → one recoverable version holding the original bytes.
    await archive_and_replace(cfg, "u1", row["id"], ops.generate_from_text("Edited."))

    import lazyclaw.gateway.routes.pdf as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(pdf_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app), row["id"]


@pytest.mark.asyncio
async def test_list_versions(client):
    tc, pid = client
    r = tc.get(f"/api/pdf/{pid}/versions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["versions"][0]["id"]


@pytest.mark.asyncio
async def test_list_versions_404_for_unknown_pdf(client):
    tc, _ = client
    assert tc.get("/api/pdf/nope/versions").status_code == 404


@pytest.mark.asyncio
async def test_restore_version(client):
    tc, pid = client
    vid = tc.get(f"/api/pdf/{pid}/versions").json()["versions"][0]["id"]
    r = tc.post(f"/api/pdf/{pid}/versions/{vid}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["file"]["id"] == pid
    # Live bytes are the restored original again.
    raw = tc.get(f"/api/pdf/{pid}/raw")
    assert raw.status_code == 200
    assert b"%PDF" in raw.content


@pytest.mark.asyncio
async def test_restore_unknown_version_404(client):
    tc, pid = client
    assert tc.post(f"/api/pdf/{pid}/versions/nope/restore").status_code == 404
