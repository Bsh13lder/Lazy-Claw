"""POST /api/docs/{id}/ai — the in-editor ✨ AI endpoint.

The Document-Specialist is stubbed so these tests stay LLM-free; they assert the
route wiring, the response envelope, scoping via get_current_user, and input
validation.
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

    import lazyclaw.gateway.routes.docs as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(docs_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app), routes_mod


@pytest.mark.asyncio
async def test_ai_edit_returns_snapshot(client, monkeypatch):
    tc, routes_mod = client
    did = tc.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]

    captured = {}

    async def _stub(config, user_id, kind, doc_id, instruction):
        captured.update(kind=kind, doc_id=doc_id, instruction=instruction, user_id=user_id)
        return SpecialistResult(ok=True, summary="Added 1 paragraph(s).",
                                snapshot={"body": {"dataStream": "Hi\r\n"}})

    monkeypatch.setattr(routes_mod, "ai_edit_document", _stub)
    r = tc.post(f"/api/docs/{did}/ai", json={"instruction": "add a hello with a link"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["snapshot"]["body"]["dataStream"] == "Hi\r\n"
    assert captured == {
        "kind": "docs", "doc_id": did, "instruction": "add a hello with a link", "user_id": "u1",
    }


@pytest.mark.asyncio
async def test_ai_edit_error_envelope(client, monkeypatch):
    tc, routes_mod = client
    did = tc.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]

    async def _stub(config, user_id, kind, doc_id, instruction):
        return SpecialistResult(ok=False, error="The AI couldn't turn that into an edit.")

    monkeypatch.setattr(routes_mod, "ai_edit_document", _stub)
    r = tc.post(f"/api/docs/{did}/ai", json={"instruction": "???"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "couldn't" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_ai_edit_empty_instruction_422(client):
    tc, _ = client
    did = tc.post("/api/docs", json={"name": "Letter"}).json()["doc"]["id"]
    assert tc.post(f"/api/docs/{did}/ai", json={"instruction": ""}).status_code == 422
    assert tc.post(f"/api/docs/{did}/ai", json={}).status_code == 422
