"""Tests for HeartbeatDaemon._check_conversations() — Task E6.

The daemon holds no registry/eco_router/permission_checker so
_conversation_deps passes None for all three.  autonomous_conversation.step
guards registry-is-None internally (returns early without raising).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.comms import conversation_store as cs
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db

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


async def test_tick_steps_due_conversations(config, user_id):
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    c = await cs.create_conversation(
        config,
        user_id,
        channel="whatsapp",
        contact_handle="+1",
        contact_name="A",
        goal="g",
    )
    await cs.update_conversation(
        config,
        user_id,
        c["id"],
        status="running",
        next_poll_at="2000-01-01T00:00:00+00:00",
    )

    daemon = HeartbeatDaemon(config=config, lane_queue=None)

    with patch(
        "lazyclaw.heartbeat.daemon.autonomous_conversation.step",
        new=AsyncMock(return_value={}),
    ) as step_mock:
        await daemon._check_conversations()

    step_mock.assert_awaited()


async def test_check_conversations_skips_not_due(config, user_id):
    """Conversations whose next_poll_at is in the future are not stepped."""
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    c = await cs.create_conversation(
        config,
        user_id,
        channel="whatsapp",
        contact_handle="+2",
        contact_name="B",
        goal="g2",
    )
    # Schedule far in the future
    await cs.update_conversation(
        config,
        user_id,
        c["id"],
        status="running",
        next_poll_at="2099-01-01T00:00:00+00:00",
    )

    daemon = HeartbeatDaemon(config=config, lane_queue=None)

    with patch(
        "lazyclaw.heartbeat.daemon.autonomous_conversation.step",
        new=AsyncMock(return_value={}),
    ) as step_mock:
        await daemon._check_conversations()

    step_mock.assert_not_awaited()


async def test_check_conversations_exception_does_not_propagate(config, user_id):
    """A step() failure is logged and swallowed; _check_conversations returns normally."""
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    c = await cs.create_conversation(
        config,
        user_id,
        channel="whatsapp",
        contact_handle="+3",
        contact_name="C",
        goal="g3",
    )
    await cs.update_conversation(
        config,
        user_id,
        c["id"],
        status="running",
        next_poll_at="2000-01-01T00:00:00+00:00",
    )

    daemon = HeartbeatDaemon(config=config, lane_queue=None)

    with patch(
        "lazyclaw.heartbeat.daemon.autonomous_conversation.step",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        # Must not raise
        await daemon._check_conversations()


def test_conversation_deps_returns_real_deps_when_wired():
    """_conversation_deps returns the injected registry/eco_router/permission_checker."""
    from types import SimpleNamespace

    from lazyclaw.config import Config
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    fake_registry = SimpleNamespace(name="registry")
    fake_eco_router = SimpleNamespace(name="eco_router")
    fake_permission_checker = SimpleNamespace(name="permission_checker")

    cfg = Config.__new__(Config)  # avoid __init__ DB setup
    cfg.heartbeat_interval = 60

    daemon = HeartbeatDaemon(
        cfg,
        lane_queue=None,
        registry=fake_registry,
        eco_router=fake_eco_router,
        permission_checker=fake_permission_checker,
    )

    deps = daemon._conversation_deps("u1")

    assert deps.registry is fake_registry
    assert deps.eco_router is fake_eco_router
    assert deps.permission_checker is fake_permission_checker


def test_conversation_deps_returns_none_when_not_wired():
    """_conversation_deps returns None for all three when daemon has no deps injected."""
    from lazyclaw.config import Config
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    cfg = Config.__new__(Config)
    cfg.heartbeat_interval = 60

    daemon = HeartbeatDaemon(cfg, lane_queue=None)

    deps = daemon._conversation_deps("u1")

    assert deps.registry is None
    assert deps.eco_router is None
    assert deps.permission_checker is None
