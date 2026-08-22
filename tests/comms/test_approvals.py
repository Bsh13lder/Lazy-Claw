"""Tests for lazyclaw.comms.approvals — first-message approval registry."""
from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from lazyclaw.comms import approvals
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
async def test_request_and_resolve(config, user_id):
    conv = {"id": "c1", "channel": "whatsapp", "contact_handle": "+1", "goal": "g"}
    with patch.object(approvals, "deliver", new=AsyncMock()) as d:
        aid = await approvals.request_first_message_approval(config, user_id, conv, "Hi!")
        d.assert_awaited_once()
    assert "c1" in aid


@pytest.mark.asyncio
async def test_resolve_runs_callback():
    called = {}
    async def cb(approved): called["v"] = approved
    approvals.register_waiter("appr-c1", cb)
    await approvals.resolve("appr-c1", True)
    assert called["v"] is True


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_noop():
    await approvals.resolve("nonexistent", True)  # must not raise
