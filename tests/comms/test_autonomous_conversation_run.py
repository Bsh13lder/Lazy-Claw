"""Tests for autonomous_conversation._run_step — Task E5."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.comms import autonomous_conversation as cr
from lazyclaw.comms import conversation_store as cs
from lazyclaw.comms.models import Msg, ReadResult
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
    assert any(t["dir"] == "out" and t["text"] == "It's Saturday 7pm — can you make it?" for t in updated["transcript"])


@pytest.mark.asyncio
async def test_run_step_empty_next_no_out_entry(config, user_id):
    conv = await _running(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "_read_new_contact_messages",
                      new=AsyncMock(return_value=[{"sender": "Alice", "text": "hmm", "ts": "t"}])), \
         patch.object(cr, "_evaluate_goal", new=AsyncMock(return_value={"done": False, "next": ""})), \
         patch.object(cr, "_send", new=AsyncMock()) as send:
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "running"
    assert updated["iteration"] == 1
    send.assert_not_awaited()  # empty followup → no send
    assert not any(t["dir"] == "out" for t in updated["transcript"])


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


# ── _read_new_contact_messages / ChannelGateway ReadResult contract ──────────
# read_thread returns a typed ReadResult (ok / messages / error), NOT a bare
# list. These pin that contract so a future gateway change can't silently turn
# a dead channel into "the contact never replied".


def _gateway(result: ReadResult):
    return SimpleNamespace(read_thread=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_read_new_messages_returns_contact_side_only(config, user_id):
    """Own messages and already-transcribed text are filtered out."""
    conv = {"id": "c1", "user_id": user_id, "channel": "whatsapp",
            "contact_handle": "+1", "transcript": [{"dir": "out", "text": "you there?"}]}
    res = ReadResult(ok=True, messages=(
        Msg(sender="me", text="you there?", timestamp="t0", is_mine=True),
        Msg(sender="Alice", text="yes, hi", timestamp="t1", is_mine=False),
    ))
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "build_gateway", return_value=_gateway(res)):
        out = await cr._read_new_contact_messages(config, deps, conv)
    assert [m["text"] for m in out] == ["yes, hi"]


@pytest.mark.asyncio
async def test_read_new_messages_failed_read_is_not_silence(config, user_id):
    """A FAILED read (MCP down) must yield no messages so the runner backs off
    and retries — never be mistaken for 'the contact hasn't replied'."""
    conv = {"id": "c1", "user_id": user_id, "channel": "whatsapp",
            "contact_handle": "+1", "transcript": []}
    res = ReadResult(ok=False, error="unknown tool: whatsapp_read")
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "build_gateway", return_value=_gateway(res)):
        out = await cr._read_new_contact_messages(config, deps, conv)
    assert out == []
