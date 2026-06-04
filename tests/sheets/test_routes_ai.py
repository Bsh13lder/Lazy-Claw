"""POST /api/sheets/{id}/ai — the in-editor ✨ AI endpoint (specialist stubbed)."""

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

    import lazyclaw.gateway.routes.sheets as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(sheets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app), routes_mod


@pytest.mark.asyncio
async def test_ai_edit_returns_snapshot(client, monkeypatch):
    tc, routes_mod = client
    sid = tc.post("/api/sheets", json={"name": "Budget"}).json()["sheet"]["id"]

    async def _stub(config, user_id, kind, doc_id, instruction):
        assert kind == "sheets"
        return SpecialistResult(ok=True, summary="Updated 1 cell(s).",
                                snapshot={"sheets": {"x": {}}})

    monkeypatch.setattr(routes_mod, "ai_edit_document", _stub)
    r = tc.post(f"/api/sheets/{sid}/ai", json={"instruction": "total column B"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "sheets" in r.json()["snapshot"]


@pytest.mark.asyncio
async def test_ai_edit_validation(client):
    tc, _ = client
    sid = tc.post("/api/sheets", json={"name": "Budget"}).json()["sheet"]["id"]
    assert tc.post(f"/api/sheets/{sid}/ai", json={"instruction": ""}).status_code == 422
