"""POST /api/pdf/{id}/ai — the in-editor ✨ AI endpoint (specialist stubbed).

PDF ops are immutable, so the route returns ``new_pdf_id`` for the viewer to
switch to rather than a snapshot.
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
from lazyclaw.pdf.store import save_pdf
from lazyclaw.runtime.doc_specialist import SpecialistResult


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
    row = await save_pdf(cfg, "u1", "doc.pdf", ops.generate_from_text("hi", title="T"))

    import lazyclaw.gateway.routes.pdf as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(pdf_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app), routes_mod, row["id"]


@pytest.mark.asyncio
async def test_ai_edit_returns_new_pdf_id(client, monkeypatch):
    tc, routes_mod, pid = client

    async def _stub(config, user_id, kind, doc_id, instruction):
        assert kind == "pdf" and doc_id == pid
        return SpecialistResult(ok=True, summary="Stamped 1 item(s).", new_id="new-123")

    monkeypatch.setattr(routes_mod, "ai_edit_document", _stub)
    r = tc.post(f"/api/pdf/{pid}/ai", json={"instruction": "sign it bottom left"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["new_pdf_id"] == "new-123"


@pytest.mark.asyncio
async def test_ai_edit_validation(client):
    tc, _, pid = client
    assert tc.post(f"/api/pdf/{pid}/ai", json={"instruction": ""}).status_code == 422
