"""Per-expense favorite flag (``project_expenses.is_favorite``).

``is_favorite`` is a plaintext INTEGER 0/1 (serialized to the clients as a JSON
boolean), default 0. It stars an individual expense so the web/mobile overview
can show a "starred only" total. Additive + backward-compatible: an expense
created without it is un-starred and serializes fine everywhere.

Covers create (defaults un-starred) / update (the star toggle) / list and the
``GET /api/budgets/changes`` delta feed (so the mobile sync pulls it). Mirrors
``test_project_is_favorite.py`` for the expense table.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.budgets import store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.budgets import router as budgets_router

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def client(cfg, monkeypatch):
    import lazyclaw.gateway.routes.budgets as routes_mod

    monkeypatch.setattr(routes_mod, "_config", cfg)
    app = FastAPI()
    app.include_router(budgets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app)


async def _project(cfg) -> str:
    proj = await store.create_project(cfg, "u1", "Alpha")
    return proj["id"]


# ---------------------------------------------------------------------------
# Store-level
# ---------------------------------------------------------------------------


async def test_create_expense_defaults_unstarred(cfg):
    pid = await _project(cfg)
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, description="x")
    assert exp["is_favorite"] is False
    fetched = await store.list_expenses(cfg, "u1", project_id=pid)
    assert fetched[0]["is_favorite"] is False


async def test_update_expense_sets_favorite(cfg):
    pid = await _project(cfg)
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, description="x")
    ok = await store.update_expense(cfg, "u1", exp["id"], is_favorite=True)
    assert ok is True
    fetched = await store.list_expenses(cfg, "u1", project_id=pid)
    assert fetched[0]["is_favorite"] is True


async def test_update_expense_clears_favorite(cfg):
    pid = await _project(cfg)
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, description="x")
    await store.update_expense(cfg, "u1", exp["id"], is_favorite=True)
    await store.update_expense(cfg, "u1", exp["id"], is_favorite=False)
    fetched = await store.list_expenses(cfg, "u1", project_id=pid)
    assert fetched[0]["is_favorite"] is False


async def test_favorite_stored_plaintext_integer(cfg):
    pid = await _project(cfg)
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, description="x")
    await store.update_expense(cfg, "u1", exp["id"], is_favorite=True)
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT is_favorite FROM project_expenses WHERE id = ?", (exp["id"],)
        )
        raw = (await cur.fetchone())[0]
    assert raw == 1, "is_favorite must be a plaintext INTEGER 0/1"


async def test_list_all_expenses_includes_favorite(cfg):
    pid = await _project(cfg)
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, description="x")
    await store.update_expense(cfg, "u1", exp["id"], is_favorite=True)
    rows = await store.list_all_expenses(cfg, "u1")
    match = next(e for e in rows if e["id"] == exp["id"])
    assert match["is_favorite"] is True


async def test_get_budget_changes_includes_expense_favorite(cfg):
    pid = await _project(cfg)
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, description="x")
    await store.update_expense(cfg, "u1", exp["id"], is_favorite=True)
    result = await store.get_budget_changes(cfg, "u1", since=None)
    match = next(e for e in result["expenses"] if e["id"] == exp["id"])
    assert "is_favorite" in match
    assert match["is_favorite"] is True


# ---------------------------------------------------------------------------
# Route-level
# ---------------------------------------------------------------------------


async def test_route_patch_expense_favorite_toggle(client):
    pid = client.post(
        "/api/budgets/projects", json={"name": "nima"}
    ).json()["project"]["id"]
    eid = client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 10.0, "description": "x"},
    ).json()["expense"]["id"]

    r = client.patch(
        f"/api/budgets/expenses/{eid}", json={"is_favorite": True}
    )
    assert r.status_code == 200, r.text

    # Read it back via the per-project list and confirm the star stuck.
    listed = client.get(f"/api/budgets/projects/{pid}/expenses").json()["expenses"]
    assert listed[0]["is_favorite"] is True

    # Un-star (False is not None → kept by the route's filter).
    r2 = client.patch(
        f"/api/budgets/expenses/{eid}", json={"is_favorite": False}
    )
    assert r2.status_code == 200, r2.text
    listed2 = client.get(
        f"/api/budgets/projects/{pid}/expenses"
    ).json()["expenses"]
    assert listed2[0]["is_favorite"] is False


async def test_route_changes_includes_expense_favorite(client):
    pid = client.post(
        "/api/budgets/projects", json={"name": "nima"}
    ).json()["project"]["id"]
    eid = client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 10.0, "description": "x"},
    ).json()["expense"]["id"]
    client.patch(f"/api/budgets/expenses/{eid}", json={"is_favorite": True})

    r = client.get("/api/budgets/changes")
    assert r.status_code == 200
    exps = r.json()["expenses"]
    assert any(e.get("is_favorite") is True for e in exps)
