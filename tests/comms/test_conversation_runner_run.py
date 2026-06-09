"""Tests for conversation_runner._run_step — Task E5."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.comms import conversation_runner as cr
from lazyclaw.comms import conversation_store as cs
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo!!")
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
def user_id():
    return "u1"


async def _running(config, user_id, *, iteration=0, max_iter=20):
    conv = await cr.start(config, user_id, channel="whatsapp", contact="+1", goal="coming to birthday?")
    conv = await cs.update_conversation(config, user_id, conv["id"], status="running",
        iteration=iteration, max_iterations=max_iter, next_poll_at="2000-01-01T00:00:00+00:00")
    return conv


@pytest.mark.asyncio
async def test_run_step_finishes_when_goal_met(config, user_id):
    conv = await _running(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "_read_new_contact_messages",
                      new=AsyncMock(return_value=[{"sender": "Alice", "text": "yes!", "ts": "10:01"}])), \
         patch.object(cr, "_evaluate_goal",
                      new=AsyncMock(return_value={"done": True, "answer": "Yes, Alice is coming"})), \
         patch.object(cr, "deliver", new=AsyncMock()) as d:
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "done"
    assert "Yes" in (updated["result"] or "")
    d.assert_awaited()


@pytest.mark.asyncio
async def test_run_step_sends_followup_when_not_done(config, user_id):
    conv = await _running(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "_read_new_contact_messages",
                      new=AsyncMock(return_value=[{"sender": "Alice", "text": "when is it?", "ts": "10:01"}])), \
         patch.object(cr, "_evaluate_goal",
                      new=AsyncMock(return_value={"done": False, "next": "It's Saturday 7pm — can you make it?"})), \
         patch.object(cr, "_send", new=AsyncMock(return_value=True)) as send:
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "running"
    assert updated["iteration"] == 1
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_step_no_reply_backs_off(config, user_id):
    conv = await _running(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "_read_new_contact_messages", new=AsyncMock(return_value=[])):
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "running"
    assert updated["next_poll_at"] is not None  # rescheduled, not finished


@pytest.mark.asyncio
async def test_run_step_max_iterations_fails(config, user_id):
    conv = await _running(config, user_id, iteration=20, max_iter=20)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "deliver", new=AsyncMock()):
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "failed"
