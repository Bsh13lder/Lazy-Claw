"""/api/budgets — project + expense CRUD from the Web UI.

Covers the happy-path create/list/expense flow, the 409 on deleting a project
that still has expenses, and the spending report shape.
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
from lazyclaw.gateway.routes.budgets import router as budgets_router


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

    import lazyclaw.gateway.routes.budgets as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(budgets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_create_project_and_expense_flow(client) -> None:
    tc = client
    r = tc.post("/api/budgets/projects", json={"name": "nima", "budget": 500})
    assert r.status_code == 200, r.text
    project = r.json()["project"]
    assert project["name"] == "nima"
    assert project["budget"] == 500

    pid = project["id"]
    r = tc.post(f"/api/budgets/projects/{pid}/expenses", json={
        "amount": 40, "description": "hosting",
    })
    assert r.status_code == 200, r.text
    assert r.json()["expense"]["amount"] == 40

    r = tc.get("/api/budgets/projects?status=active")
    assert r.status_code == 200
    rolled = r.json()["projects"][0]
    assert rolled["spent"] == 40
    assert rolled["remaining"] == 460


@pytest.mark.asyncio
async def test_delete_project_with_expenses_returns_409(client) -> None:
    tc = client
    pid = tc.post("/api/budgets/projects", json={"name": "nima", "budget": 100}).json()["project"]["id"]
    tc.post(f"/api/budgets/projects/{pid}/expenses", json={"amount": 10})

    r = tc.delete(f"/api/budgets/projects/{pid}")
    assert r.status_code == 409

    r = tc.delete(f"/api/budgets/projects/{pid}?cascade=true")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_set_budget_and_report(client) -> None:
    tc = client
    pid = tc.post("/api/budgets/projects", json={"name": "nima"}).json()["project"]["id"]
    r = tc.put(f"/api/budgets/projects/{pid}/budget", json={"budget": 750})
    assert r.status_code == 200
    assert r.json()["project"]["budget"] == 750

    r = tc.get("/api/budgets/report")
    assert r.status_code == 200
    body = r.json()
    assert body["total_budget"] == 750
    assert body["projects"][0]["name"] == "nima"
