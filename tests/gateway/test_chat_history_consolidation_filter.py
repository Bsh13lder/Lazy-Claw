"""Regression test for GET /api/chat/sessions/{id}/messages consolidation filter.

2026-07-01 incident: the brain fan-out CONSOLIDATION turn
(task_runner._consolidate) enqueues a user-role message whose content is the
internal "[Background fan-out complete — N tasks finished] … Write ONE
consolidated summary …" instruction. Nothing filtered it, so it was persisted
to agent_messages and re-rendered in the mobile/web chat as a green user
bubble (with the brain chatting back to its own instruction). The endpoint
must hide these synthetic user turns while keeping the brain's assistant
summary reply visible.
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
from lazyclaw.runtime.consolidation_guidance import CONSOLIDATION_TURN_PREFIX


SESSION_ID = "sess-1"

_CONSOLIDATION_MSG = (
    f"{CONSOLIDATION_TURN_PREFIX} — 2 tasks finished]\n\n"
    "Results from background tasks you spawned earlier:\n\n"
    "## Task 1: check upwork\nI did not fabricate a result.\n\n"
    "Write ONE consolidated summary for the user. Don't repeat raw blobs — "
    "synthesize. Keep it tight (~6-12 lines for Telegram)."
)


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
        rows = [
            # (id, role, content)
            ("msg-000", "user", "check my upwork and my email"),
            ("msg-001", "assistant", "On it — dispatching two background tasks."),
            # The synthetic consolidation prompt — MUST be hidden.
            ("msg-002", "user", _CONSOLIDATION_MSG),
            # The brain's consolidated summary reply — MUST stay visible.
            ("msg-003", "assistant", "Upwork: 1 new offer. Email: nothing urgent."),
        ]
        for i, (mid, role, content) in enumerate(rows):
            await db.execute(
                "INSERT INTO agent_messages "
                "(id, user_id, chat_session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, "u1", SESSION_ID, role, content, f"2026-07-01 10:00:0{i}"),
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


def _messages(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()["messages"]


@pytest.mark.asyncio
async def test_consolidation_user_turn_is_hidden(client) -> None:
    msgs = _messages(client.get(f"/api/chat/sessions/{SESSION_ID}/messages?limit=50"))
    ids = [m["id"] for m in msgs]
    # The synthetic consolidation user turn is filtered out …
    assert "msg-002" not in ids
    # … while the real user turn and BOTH assistant replies remain, in order.
    assert ids == ["msg-000", "msg-001", "msg-003"]
    # And no visible message carries the internal instruction text.
    assert not any(CONSOLIDATION_TURN_PREFIX in m["content"] for m in msgs)


@pytest.mark.asyncio
async def test_assistant_summary_survives(client) -> None:
    msgs = _messages(client.get(f"/api/chat/sessions/{SESSION_ID}/messages?limit=50"))
    summary = next(m for m in msgs if m["id"] == "msg-003")
    assert "Upwork: 1 new offer" in summary["content"]
