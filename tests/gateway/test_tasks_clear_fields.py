"""PATCH /api/tasks/{id} must let a client CLEAR a field with an explicit null.

Regression: the route used ``{k: v for ... if v is not None}``, which conflated
"field absent" (leave alone) with "field is null" (clear). The web sends
``{"due_date": null}`` to clear — that became an all-None dump → 400 "No fields
to update" (swallowed by the web), so a cleared due date / reminder / note
silently reappeared. The store already writes NULL for a None value, so the fix
is route-level: ``model_dump(exclude_unset=True)`` keeps explicit nulls while a
truly-omitted field is left untouched. Mobile never sends explicit nulls, so it
is unaffected — pinned by ``test_omitted_field_is_left_untouched``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes import tasks as tasks_mod


async def _setup_db(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    return cfg


def _fake_user() -> User:
    return User(id="u1", username="alice", display_name=None,
                encryption_salt="salt-a", role="user")


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    cfg = await _setup_db(tmp_path)
    monkeypatch.setattr(tasks_mod, "_config", cfg)
    app = FastAPI()
    app.include_router(tasks_mod.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    try:
        yield TestClient(app)
    finally:
        await close_pool()


def _future_iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _create(client, **fields) -> dict:
    r = client.post("/api/tasks", json={"title": "t", **fields})
    assert r.status_code == 200, r.text
    return r.json()["task"]


@pytest.mark.asyncio
async def test_clear_due_date_with_explicit_null(client) -> None:
    task = _create(client, due_date=_future_iso(days=2))
    assert task["due_date"] is not None
    r = client.patch(f"/api/tasks/{task['id']}", json={"due_date": None})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["due_date"] is None


@pytest.mark.asyncio
async def test_clear_reminder_at_with_explicit_null(client) -> None:
    task = _create(client, reminder_at=_future_iso(days=2))
    assert task["reminder_at"] is not None
    r = client.patch(f"/api/tasks/{task['id']}", json={"reminder_at": None})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["reminder_at"] is None


@pytest.mark.asyncio
async def test_clear_description_with_explicit_null(client) -> None:
    task = _create(client, description="some notes")
    assert task["description"] == "some notes"
    r = client.patch(f"/api/tasks/{task['id']}", json={"description": None})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["description"] in (None, "")


@pytest.mark.asyncio
async def test_omitted_field_is_left_untouched(client) -> None:
    """Patching only description must NOT clear the due date (absent ≠ clear).

    This is the mobile contract: it sends only the fields it changed, and an
    unmentioned field must survive.
    """
    due = _future_iso(days=3)
    task = _create(client, due_date=due, description="old")
    r = client.patch(f"/api/tasks/{task['id']}", json={"description": "new"})
    assert r.status_code == 200, r.text
    out = r.json()["task"]
    assert out["description"] == "new"
    assert out["due_date"] is not None  # untouched, not cleared


@pytest.mark.asyncio
async def test_empty_patch_still_400(client) -> None:
    task = _create(client)
    r = client.patch(f"/api/tasks/{task['id']}", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_null_title_is_ignored_not_applied(client) -> None:
    """A stray null title must never blank the always-required title, but other
    fields in the same patch still apply."""
    task = _create(client, description="keep")
    r = client.patch(
        f"/api/tasks/{task['id']}", json={"title": None, "description": "changed"}
    )
    assert r.status_code == 200, r.text
    out = r.json()["task"]
    assert out["title"] == "t"  # unchanged
    assert out["description"] == "changed"
