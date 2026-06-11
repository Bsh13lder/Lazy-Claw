"""Regression tests for GET /api/chat/sessions/{id}/messages ordering.

2026-06-10 incident: the endpoint ran ``ORDER BY created_at ASC LIMIT ?``,
returning the OLDEST `limit` messages of a session instead of the newest.
The Flutter app (and web, limit=100) seed their chat screens from this
endpoint expecting the most-recent slice — users with long sessions saw
April's history (a Google Drive mass-delete + crashed create_drive_folder
turn) instead of the live conversation.

Contract under test:
  - no `before` → the NEWEST `limit` messages, returned oldest-first
  - `before=<id>` → the `limit` messages immediately PRECEDING that
    message, returned oldest-first (page upward through history)
  - rows sharing one created_at (turns are batch-persisted with a single
    timestamp) keep insertion order and aren't skipped by pagination
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
import lazyclaw.gateway.routes.chat_history as chat_history_mod


SESSION_ID = "sess-1"


def _fake_user() -> User:
    return User(id="u1", username="alice", display_name=None,
                encryption_salt="salt-a", role="user")


async def _setup_db(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.execute(
            "INSERT INTO agent_chat_sessions (id, user_id, title, is_primary) "
            "VALUES (?, ?, ?, 1)",
            (SESSION_ID, "u1", "Main"),
        )
        # 30 messages across 10 turns. Each turn is batch-persisted with ONE
        # shared created_at (matches agent.py post-loop persistence), so the
        # rowid insertion order is the only within-turn tiebreaker.
        for turn in range(10):
            ts = f"2026-06-01 10:{turn:02d}:00"
            for part in range(3):
                idx = turn * 3 + part
                await db.execute(
                    "INSERT INTO agent_messages "
                    "(id, user_id, chat_session_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"msg-{idx:03d}", "u1", SESSION_ID,
                     "user" if part == 0 else "assistant",
                     f"content-{idx:03d}", ts),
                )
        await db.commit()
    return cfg


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    cfg = await _setup_db(tmp_path)
    monkeypatch.setattr(chat_history_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(chat_history_mod.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return TestClient(app)


def _ids(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [m["id"] for m in resp.json()["messages"]]


@pytest.mark.asyncio
async def test_default_page_returns_newest_messages(client) -> None:
    """No `before` → the newest `limit` rows, oldest-first within the page."""
    ids = _ids(client.get(f"/api/chat/sessions/{SESSION_ID}/messages?limit=6"))
    assert ids == [f"msg-{i:03d}" for i in range(24, 30)]


@pytest.mark.asyncio
async def test_default_page_larger_than_session_returns_all(client) -> None:
    ids = _ids(client.get(f"/api/chat/sessions/{SESSION_ID}/messages?limit=500"))
    assert ids == [f"msg-{i:03d}" for i in range(30)]


@pytest.mark.asyncio
async def test_before_pages_immediately_preceding_slice(client) -> None:
    """`before` returns the rows just before the anchor, oldest-first."""
    ids = _ids(client.get(
        f"/api/chat/sessions/{SESSION_ID}/messages?limit=6&before=msg-024"
    ))
    assert ids == [f"msg-{i:03d}" for i in range(18, 24)]


@pytest.mark.asyncio
async def test_before_within_shared_timestamp_batch(client) -> None:
    """Anchoring mid-turn must not skip same-created_at siblings."""
    ids = _ids(client.get(
        f"/api/chat/sessions/{SESSION_ID}/messages?limit=4&before=msg-026"
    ))
    assert ids == [f"msg-{i:03d}" for i in range(22, 26)]


@pytest.mark.asyncio
async def test_paging_covers_full_history_without_gaps(client) -> None:
    """Walking `before` pages from the tail reconstructs the whole session."""
    seen: list[str] = []
    resp = client.get(f"/api/chat/sessions/{SESSION_ID}/messages?limit=7")
    page = _ids(resp)
    while page:
        seen = page + seen
        resp = client.get(
            f"/api/chat/sessions/{SESSION_ID}/messages?limit=7&before={page[0]}"
        )
        page = _ids(resp)
    assert seen == [f"msg-{i:03d}" for i in range(30)]
