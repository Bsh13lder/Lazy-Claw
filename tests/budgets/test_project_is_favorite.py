"""Per-project favorite flag (``projects.is_favorite``).

``is_favorite`` is a plaintext INTEGER 0/1 (serialized to the clients as a JSON
boolean), default 0. It pins a project into the mobile Home "Favorites" section.
Additive + backward-compatible: a project created without it is un-favorited and
serializes fine everywhere.

Covers create / update / list and the ``GET /api/budgets/changes`` delta feed
(so the mobile sync pulls it), plus the idempotent-upsert "leave as-is" semantics
(None on an existing project never clobbers a stored favorite). Mirrors
``test_project_color.py`` exactly.
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


# ---------------------------------------------------------------------------
# Store-level
# ---------------------------------------------------------------------------


async def test_create_project_with_favorite_persists(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", is_favorite=True)
    assert proj["is_favorite"] is True
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["is_favorite"] is True


async def test_create_project_without_favorite_is_false(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha")
    assert proj["is_favorite"] is False
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["is_favorite"] is False
    # Serialization in list must still work for an un-favorited project.
    projects = await store.list_projects(cfg, "u1")
    assert projects[0]["is_favorite"] is False


async def test_list_projects_includes_favorite(cfg):
    await store.create_project(cfg, "u1", "Alpha", is_favorite=True)
    projects = await store.list_projects(cfg, "u1")
    assert projects[0]["is_favorite"] is True


async def test_update_project_sets_favorite(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha")
    ok = await store.update_project(cfg, "u1", proj["id"], is_favorite=True)
    assert ok is True
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["is_favorite"] is True


async def test_update_project_clears_favorite(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", is_favorite=True)
    await store.update_project(cfg, "u1", proj["id"], is_favorite=False)
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["is_favorite"] is False


async def test_favorite_stored_plaintext_integer(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", is_favorite=True)
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT is_favorite FROM projects WHERE id = ?", (proj["id"],)
        )
        raw = (await cur.fetchone())[0]
    assert raw == 1, "is_favorite must be a plaintext INTEGER 0/1"


async def test_upsert_none_leaves_favorite_unchanged(cfg):
    # An idempotent re-create by name with is_favorite=None must NOT clobber a
    # stored favorite (mirrors color's "only update when explicitly provided").
    proj = await store.create_project(cfg, "u1", "Alpha", is_favorite=True)
    again = await store.create_project(cfg, "u1", "Alpha", budget=500.0)
    assert again["is_favorite"] is True
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["is_favorite"] is True


async def test_get_project_by_name_includes_favorite(cfg):
    await store.create_project(cfg, "u1", "Gamma", is_favorite=True)
    proj = await store.get_project_by_name(cfg, "u1", "gamma")
    assert proj["is_favorite"] is True


async def test_get_budget_changes_includes_favorite(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", is_favorite=True)
    result = await store.get_budget_changes(cfg, "u1", since=None)
    match = next(p for p in result["projects"] if p["id"] == proj["id"])
    assert "is_favorite" in match
    assert match["is_favorite"] is True


# ---------------------------------------------------------------------------
# Route-level
# ---------------------------------------------------------------------------


async def test_route_create_with_favorite(client):
    r = client.post(
        "/api/budgets/projects", json={"name": "nima", "is_favorite": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["project"]["is_favorite"] is True


async def test_route_create_without_favorite(client):
    r = client.post("/api/budgets/projects", json={"name": "nima"})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["is_favorite"] is False


async def test_route_patch_favorite_toggle(client):
    pid = client.post(
        "/api/budgets/projects", json={"name": "nima"}
    ).json()["project"]["id"]
    r = client.patch(
        f"/api/budgets/projects/{pid}", json={"is_favorite": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["project"]["is_favorite"] is True

    # Un-favorite (False is not None → kept by the route's filter).
    r2 = client.patch(
        f"/api/budgets/projects/{pid}", json={"is_favorite": False}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["project"]["is_favorite"] is False


async def test_route_changes_includes_favorite(client):
    client.post(
        "/api/budgets/projects", json={"name": "nima", "is_favorite": True}
    )
    r = client.get("/api/budgets/changes")
    assert r.status_code == 200
    projs = r.json()["projects"]
    assert any(p.get("is_favorite") is True for p in projs)
