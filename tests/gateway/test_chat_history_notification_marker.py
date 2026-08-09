"""History endpoint exposes the notification-card marker for client styling.

``GET /api/chat/sessions/{id}/messages`` items gain ``"kind": "notification"``
ONLY on rows written by the chat_card leg; every other row keeps its exact
payload shape (no ``kind`` key at all).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes import chat_history
from lazyclaw.notifications import chat_card
from lazyclaw.runtime.session_resolver import (
    get_primary_session_id,
    invalidate_primary_session,
)


async def _setup_db(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path)
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


def _make_app(cfg: Config, monkeypatch) -> FastAPI:
    monkeypatch.setattr(chat_history, "_config", cfg)
    app = FastAPI()
    app.include_router(chat_history.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return app


@pytest.fixture
async def env(tmp_path: Path, monkeypatch):
    invalidate_primary_session("u1")
    cfg = await _setup_db(tmp_path)

    # One normal assistant row + one notification card in the primary session.
    session_id = await get_primary_session_id(cfg, "u1")
    key = await get_user_dek(cfg, "u1")
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO agent_messages "
            "(id, user_id, chat_session_id, role, content, tool_name, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m-normal", "u1", session_id, "assistant",
             encrypt("a normal reply", key), None, None),
        )
        await db.commit()
    card_id = await chat_card.emit(cfg, "u1", {
        "id": "n-1", "kind": "task_reminder",
        "title": "Medicine", "body": "Take your dose now",
        "created_at": "2026-08-09T10:00:00+00:00",
    })
    assert card_id

    app = _make_app(cfg, monkeypatch)
    try:
        yield cfg, app, session_id, card_id
    finally:
        invalidate_primary_session("u1")
        await close_pool()


@pytest.mark.asyncio
async def test_marker_exposed_and_other_rows_unchanged(env):
    cfg, app, session_id, card_id = env
    with TestClient(app) as client:
        resp = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    by_id = {m["id"]: m for m in messages}

    card = by_id[card_id]
    assert card["kind"] == "notification"
    assert card["role"] == "assistant"
    assert card["content"] == "Medicine\nTake your dose now"
    assert card["tool_calls"] is None

    normal = by_id["m-normal"]
    assert "kind" not in normal, "non-notification rows keep their exact shape"
    assert normal["content"] == "a normal reply"
