"""Tests for lazyclaw.comms.conversation_runner — start() + drafting branch of step()."""
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


@pytest.mark.asyncio
async def test_start_creates_drafting_due_now(config, user_id):
    conv = await cr.start(config, user_id, channel="whatsapp",
        contact="+34600000000", goal="ask if coming to birthday")
    assert conv["status"] == "drafting"
    assert conv["next_poll_at"] is not None  # start schedules it due immediately


@pytest.mark.asyncio
async def test_step_drafting_requests_approval(config, user_id):
    conv = await cr.start(config, user_id, channel="whatsapp", contact="+1", goal="ask X")
    deps = SimpleNamespace(registry=None, eco_router=None, permission_checker=None)
    with patch.object(cr, "_draft_message", new=AsyncMock(return_value="Hi! Quick q…")), \
         patch.object(cr, "_request_approval", new=AsyncMock(return_value="appr-1")):
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "awaiting_approval"
    assert updated["approval_id"] == "appr-1"
    assert updated["next_poll_at"] is None  # heartbeat must not re-pick while awaiting
    assert updated["transcript"][-1]["dir"] == "draft"
    assert updated["transcript"][-1]["text"] == "Hi! Quick q…"


@pytest.mark.asyncio
async def test_step_drafting_fail_when_no_draft(config, user_id):
    conv = await cr.start(config, user_id, channel="whatsapp", contact="+1", goal="g")
    deps = SimpleNamespace(registry=None, eco_router=None, permission_checker=None)
    with patch.object(cr, "_draft_message", new=AsyncMock(return_value=None)), \
         patch.object(cr, "deliver", new=AsyncMock()) as deliver_mock:
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "failed"
    deliver_mock.assert_awaited_once()
    assert deliver_mock.await_args.kwargs["kind"] == "conversation_result"
