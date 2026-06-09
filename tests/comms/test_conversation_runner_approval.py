"""Tests for conversation_runner.on_approval — Task E4."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.comms import conversation_runner as cr
from lazyclaw.comms import conversation_store as cs
from lazyclaw.comms.models import SendResult
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


async def _drafted(config, user_id):
    """Create a conversation already in awaiting_approval with a draft transcript entry."""
    conv = await cr.start(config, user_id, channel="whatsapp", contact="+1", goal="g")
    conv = await cs.update_conversation(
        config, user_id, conv["id"], status="awaiting_approval",
        approval_id="appr-x", next_poll_at=None,
        append_transcript={"dir": "draft", "text": "Hi Alice!", "ts": "t"},
    )
    return conv


@pytest.mark.asyncio
async def test_on_approval_approve_sends_and_runs(config, user_id):
    conv = await _drafted(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    fake_gw = SimpleNamespace(send=AsyncMock(return_value=SendResult(ok=True)))
    with patch.object(cr, "build_gateway", return_value=fake_gw):
        updated = await cr.on_approval(config, deps, conv, True)
    assert updated["status"] == "running"
    assert updated["next_poll_at"] is not None
    assert updated["transcript"][-1]["dir"] == "out"
    fake_gw.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_approval_deny_aborts(config, user_id):
    conv = await _drafted(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "deliver", new=AsyncMock()):
        updated = await cr.on_approval(config, deps, conv, False)
    assert updated["status"] == "aborted"


@pytest.mark.asyncio
async def test_on_approval_send_failure_fails(config, user_id):
    conv = await _drafted(config, user_id)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    fake_gw = SimpleNamespace(send=AsyncMock(return_value=SendResult(ok=False, error="blocked")))
    with patch.object(cr, "build_gateway", return_value=fake_gw), \
         patch.object(cr, "deliver", new=AsyncMock()):
        updated = await cr.on_approval(config, deps, conv, True)
    assert updated["status"] == "failed"


@pytest.mark.asyncio
async def test_on_approval_no_draft_fails(config, user_id):
    # awaiting_approval but transcript has NO draft entry
    conv = await cr.start(config, user_id, channel="whatsapp", contact="+1", goal="g")
    conv = await cs.update_conversation(config, user_id, conv["id"], status="awaiting_approval", next_poll_at=None)
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "deliver", new=AsyncMock()):
        updated = await cr.on_approval(config, deps, conv, True)
    assert updated["status"] == "failed"
