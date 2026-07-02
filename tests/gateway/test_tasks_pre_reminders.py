"""REST/mobile task-create must derive pre_reminders from the user's offsets.

Crux for mobile: ``POST /api/tasks`` calls ``store.create_task`` directly and
previously computed NO advance reminders — the offset→pre_reminders logic only
ran on the chat/Telegram skill path. This pins that a timed task created over
REST gets the same Proton-Calendar-style advance reminders, and that an
explicit ``pre_reminders: []`` opts out.
"""
from __future__ import annotations

import json
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
    # The tasks route reads a module-level `_config` (not via Depends).
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


@pytest.mark.asyncio
async def test_timed_task_gets_default_pre_reminders(client) -> None:
    """A timed task (no priority hint) gets the default 2 advance reminders."""
    reminder = _future_iso(days=3)
    r = client.post("/api/tasks", json={"title": "water the plants", "reminder_at": reminder})
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["pre_reminders"] is not None
    entries = json.loads(task["pre_reminders"])
    assert len(entries) == 2  # default offsets ["-2h", "-1h"]
    base = datetime.fromisoformat(reminder)
    now = datetime.now(timezone.utc)
    for e in entries:
        dt = datetime.fromisoformat(e)
        assert now < dt < base


@pytest.mark.asyncio
async def test_task_without_reminder_has_no_pre_reminders(client) -> None:
    r = client.post("/api/tasks", json={"title": "someday idea"})
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["pre_reminders"] is None


@pytest.mark.asyncio
async def test_explicit_empty_pre_reminders_opts_out(client) -> None:
    reminder = _future_iso(days=3)
    r = client.post(
        "/api/tasks",
        json={"title": "quiet task", "reminder_at": reminder, "pre_reminders": []},
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["pre_reminders"] is None


@pytest.mark.asyncio
async def test_explicit_offset_pre_reminders_override(client) -> None:
    reminder = _future_iso(days=3)
    r = client.post(
        "/api/tasks",
        json={"title": "custom task", "reminder_at": reminder, "pre_reminders": ["-15m"]},
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    entries = json.loads(task["pre_reminders"])
    assert len(entries) == 1
    base = datetime.fromisoformat(reminder)
    assert datetime.fromisoformat(entries[0]) == base - timedelta(minutes=15)


@pytest.mark.asyncio
async def test_pre_reminders_respect_configured_offsets(client) -> None:
    """A user who configures 3 offsets gets 3 advance reminders on REST create."""
    from lazyclaw.settings.general import update_general_settings

    await update_general_settings(
        tasks_mod._config, "u1", {"reminder_offsets": ["-1d", "-3h", "-30m"]}
    )
    reminder = _future_iso(days=3)
    r = client.post("/api/tasks", json={"title": "big meeting", "reminder_at": reminder})
    assert r.status_code == 200, r.text
    entries = json.loads(r.json()["task"]["pre_reminders"])
    assert len(entries) == 3
