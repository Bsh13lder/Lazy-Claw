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
async def test_list_all_expenses_endpoint(client) -> None:
    tc = client
    pa = tc.post("/api/budgets/projects", json={"name": "Alpha", "budget": 100}).json()["project"]
    pb = tc.post("/api/budgets/projects", json={"name": "Beta", "budget": 100}).json()["project"]
    tc.post(f"/api/budgets/projects/{pa['id']}/expenses", json={"amount": 10, "description": "x"})
    tc.post(f"/api/budgets/projects/{pb['id']}/expenses", json={"amount": 20, "description": "y"})

    r = tc.get("/api/budgets/expenses")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    names = {e["project_name"] for e in body["expenses"]}
    assert names == {"Alpha", "Beta"}
    assert all("project_id" in e and "project_name" in e for e in body["expenses"])


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


@pytest.mark.asyncio
async def test_patch_expense_moves_project(client) -> None:
    tc = client
    a = tc.post("/api/budgets/projects", json={"name": "General"}).json()["project"]
    b = tc.post("/api/budgets/projects", json={"name": "ClubBay"}).json()["project"]
    e = tc.post(
        f"/api/budgets/projects/{a['id']}/expenses",
        json={"amount": 12, "description": "coffee"},
    ).json()["expense"]

    r = tc.patch(f"/api/budgets/expenses/{e['id']}", json={"project_id": b["id"]})
    assert r.status_code == 200, r.text

    # The move persisted server-side (this was silently dropped before).
    moved = tc.get(f"/api/budgets/projects/{b['id']}/expenses").json()["expenses"]
    assert [x["id"] for x in moved] == [e["id"]]
    assert tc.get(f"/api/budgets/projects/{a['id']}/expenses").json()["expenses"] == []


@pytest.mark.asyncio
async def test_patch_expense_rejects_bad_project(client) -> None:
    tc = client
    a = tc.post("/api/budgets/projects", json={"name": "General"}).json()["project"]
    e = tc.post(
        f"/api/budgets/projects/{a['id']}/expenses",
        json={"amount": 5, "description": "x"},
    ).json()["expense"]

    assert tc.patch(
        f"/api/budgets/expenses/{e['id']}", json={"project_id": "nope"}
    ).status_code == 404
    assert tc.patch(
        f"/api/budgets/expenses/{e['id']}", json={"project_id": None}
    ).status_code == 400


@pytest.mark.asyncio
async def test_inbox_suggestions_endpoint(client, monkeypatch) -> None:
    tc = client
    tc.post("/api/budgets/projects", json={"name": "General"})
    club = tc.post("/api/budgets/projects", json={"name": "ClubBay"}).json()["project"]
    gen = next(p for p in tc.get("/api/budgets/projects").json()["projects"]
               if p["name_key"] == "general")
    e = tc.post(f"/api/budgets/projects/{gen['id']}/expenses",
                json={"amount": 50, "description": "venue deposit"}).json()["expense"]

    from lazyclaw.budgets.inbox_suggest import ExpenseSuggestion
    import lazyclaw.gateway.routes.budgets as routes_mod

    async def fake_suggest(config, user_id, **kw):
        return ExpenseSuggestion("ClubBay", "high", "club spend", "llm")
    monkeypatch.setattr(routes_mod.inbox_suggest, "suggest_expense_project", fake_suggest)

    r = tc.post("/api/budgets/inbox/suggestions", json={})
    assert r.status_code == 200, r.text
    assert r.json()["suggestions"] == [{
        "expense_id": e["id"], "project_id": club["id"], "project_name": "ClubBay",
        "confidence": "high", "reason": "club spend",
    }]


@pytest.mark.asyncio
async def test_inbox_suggestions_empty_expense_ids_means_none(client, monkeypatch) -> None:
    """An explicit ``expense_ids: []`` must mean "suggest for NONE of them",
    not "absent" (which means ALL). Regression: ``if body.expense_ids:`` was
    falsy for an empty list, silently falling through to "suggest for the
    whole inbox"."""
    tc = client
    tc.post("/api/budgets/projects", json={"name": "General"})
    gen = next(p for p in tc.get("/api/budgets/projects").json()["projects"]
               if p["name_key"] == "general")
    tc.post(f"/api/budgets/projects/{gen['id']}/expenses",
            json={"amount": 50, "description": "venue deposit"})

    import lazyclaw.gateway.routes.budgets as routes_mod

    calls: list[int] = []

    async def counting_suggest(config, user_id, **kw):
        calls.append(1)
        from lazyclaw.budgets.inbox_suggest import ExpenseSuggestion
        return ExpenseSuggestion(None, "none", None, "none")
    monkeypatch.setattr(routes_mod.inbox_suggest, "suggest_expense_project", counting_suggest)

    r = tc.post("/api/budgets/inbox/suggestions", json={"expense_ids": []})
    assert r.status_code == 200, r.text
    assert r.json() == {"suggestions": [], "skipped": 0}
    assert calls == []
