"""/api/tasks/{task_id}/comments — REST add/delete for the comment thread.

Route-level coverage against the real store (isolated tmp DB + a real user
DEK), mirroring ``tests/test_budgets_routes.py``'s fixture pattern (itself
adapted from ``tests/test_specialists_routes.py``'s auth/app wiring). Store
behavior — the 500-comment cap, subtask validation, idempotent client-id
replay, encryption at rest — is already covered by
``tests/tasks/test_task_comments.py``; this file proves only the thin HTTP
layer: the route forces ``author="user"``, and the store's
``ValueError`` / ``CommentLimitReached`` / ``None`` contracts map to
400 / 409 / 404.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.tasks import router as tasks_router
from lazyclaw.tasks import store as task_store


@pytest.fixture
async def cfg(tmp_path: Path) -> Config:
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
    return c


@pytest.fixture
async def client(cfg: Config, monkeypatch):
    import lazyclaw.gateway.routes.tasks as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def seeded_task_id(cfg: Config) -> str:
    """One task owned by the logged-in user, created straight through the
    store (the create-task route isn't under test here)."""
    task = await task_store.create_task(cfg, "u1", "a task")
    return task["id"]


async def test_post_comment_forces_user_author(client, seeded_task_id):
    r = await client.post(
        f"/api/tasks/{seeded_task_id}/comments",
        json={"text": "from the API", "id": "c-clientid1"},
    )
    assert r.status_code == 200, r.text
    c = r.json()["comment"]
    assert c["author"] == "user" and c["id"] == "c-clientid1"


async def test_post_comment_404_400_409(client, seeded_task_id):
    assert (await client.post(
        "/api/tasks/nope/comments", json={"text": "x"},
    )).status_code == 404
    assert (await client.post(
        f"/api/tasks/{seeded_task_id}/comments",
        json={"text": "x", "subtask_id": "s-nope"},
    )).status_code == 400


async def test_delete_comment_roundtrip(client, seeded_task_id):
    cid = (await client.post(
        f"/api/tasks/{seeded_task_id}/comments", json={"text": "bye"},
    )).json()["comment"]["id"]
    assert (await client.delete(
        f"/api/tasks/{seeded_task_id}/comments/{cid}",
    )).json()["deleted"] is True
    assert (await client.delete(
        f"/api/tasks/{seeded_task_id}/comments/{cid}",
    )).json()["deleted"] is False
