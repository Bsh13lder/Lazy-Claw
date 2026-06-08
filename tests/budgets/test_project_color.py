"""Per-project color field (feat/flutter-mobile calendar color-coding).

``color`` is an optional hex string like ``"#4F8AF4"`` (or null), stored
PLAINTEXT like ``status``/``budget`` (it is not sensitive). Additive +
backward-compatible: a project created without a color has ``color=None`` and
serializes fine everywhere.

Covers create / update / list and the ``GET /api/budgets/changes`` delta feed
(so the mobile sync pulls it), plus lenient validation — a bad color never 500s,
it is cleared to None.
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


async def test_create_project_with_color_persists(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", color="#4F8AF4")
    assert proj["color"] == "#4F8AF4"
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["color"] == "#4F8AF4"


async def test_create_project_without_color_is_none(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha")
    assert proj["color"] is None
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["color"] is None
    # Serialization in list must still work for a colorless project.
    projects = await store.list_projects(cfg, "u1")
    assert projects[0]["color"] is None


async def test_list_projects_includes_color(cfg):
    await store.create_project(cfg, "u1", "Alpha", color="#FF0000")
    projects = await store.list_projects(cfg, "u1")
    assert projects[0]["color"] == "#FF0000"


async def test_update_project_sets_color(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha")
    ok = await store.update_project(cfg, "u1", proj["id"], color="#00FF00")
    assert ok is True
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["color"] == "#00FF00"


async def test_update_project_changes_color(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", color="#111111")
    await store.update_project(cfg, "u1", proj["id"], color="#222222")
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["color"] == "#222222"


async def test_color_stored_plaintext(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", color="#4F8AF4")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT color FROM projects WHERE id = ?", (proj["id"],)
        )
        raw = (await cur.fetchone())[0]
    assert raw == "#4F8AF4", "color must be plaintext (not enc:)"


async def test_invalid_color_cleared_on_create(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", color="not-a-color")
    assert proj["color"] is None


async def test_invalid_color_cleared_on_update(cfg):
    proj = await store.create_project(cfg, "u1", "Beta", color="#ABCDEF")
    ok = await store.update_project(cfg, "u1", proj["id"], color="bad")
    assert ok is True
    fetched = await store.get_project(cfg, "u1", proj["id"])
    assert fetched["color"] is None


async def test_get_project_by_name_includes_color(cfg):
    await store.create_project(cfg, "u1", "Gamma", color="#ABCDEF")
    proj = await store.get_project_by_name(cfg, "u1", "gamma")
    assert proj["color"] == "#ABCDEF"


async def test_get_budget_changes_includes_color(cfg):
    proj = await store.create_project(cfg, "u1", "Alpha", color="#123ABC")
    result = await store.get_budget_changes(cfg, "u1", since=None)
    match = next(p for p in result["projects"] if p["id"] == proj["id"])
    assert "color" in match
    assert match["color"] == "#123ABC"


# ---------------------------------------------------------------------------
# Route-level
# ---------------------------------------------------------------------------


async def test_route_create_with_color(client):
    r = client.post("/api/budgets/projects", json={"name": "nima", "color": "#4F8AF4"})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["color"] == "#4F8AF4"


async def test_route_create_without_color(client):
    r = client.post("/api/budgets/projects", json={"name": "nima"})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["color"] is None


async def test_route_patch_color(client):
    pid = client.post(
        "/api/budgets/projects", json={"name": "nima"}
    ).json()["project"]["id"]
    r = client.patch(f"/api/budgets/projects/{pid}", json={"color": "#0A0B0C"})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["color"] == "#0A0B0C"


async def test_route_changes_includes_color(client):
    client.post("/api/budgets/projects", json={"name": "nima", "color": "#4F8AF4"})
    r = client.get("/api/budgets/changes")
    assert r.status_code == 200
    projs = r.json()["projects"]
    assert any(p.get("color") == "#4F8AF4" for p in projs)


async def test_route_bad_color_does_not_500(client):
    r = client.post("/api/budgets/projects", json={"name": "nima", "color": "blue"})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["color"] is None
