"""Per-channel standing instructions (Phase 5).

A channel instruction is the `auto_reply` field on the channel's MCP watcher
job — the existing heartbeat path then runs it as a real agent turn whenever
new messages arrive (execution result is pushed via the 📨 notifier, which is
preference-aware). This module gives it CRUD:

  - set: update the channel-wide watcher's auto_reply, or create a fresh
    indefinite watcher when none exists
  - get: read it back (decrypted)
  - clear: drop auto_reply but keep the watcher notifying
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.comms import channel_instructions as ci
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.orchestrator import create_job, get_job, list_jobs

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _seed_watcher(config, service="whatsapp", auto_reply=None, tool_args=None):
    from lazyclaw.heartbeat.mcp_watcher import build_mcp_watcher_context
    ctx = build_mcp_watcher_context(
        service=service,
        tool_name=f"{service}_read",
        tool_args=tool_args or {"limit": 10},
        check_interval=120,
        auto_reply=auto_reply,
    )
    return await create_job(
        config, "u1",
        name=f"Watch: {service}",
        instruction=f"Watch {service} for new messages",
        job_type="watcher",
        context=ctx,
    )


async def test_get_returns_none_when_no_watcher(config):
    assert await ci.get_channel_instruction(config, "u1", "whatsapp") is None


async def test_set_updates_existing_watcher(config):
    job_id = await _seed_watcher(config, auto_reply=None)
    result = await ci.set_channel_instruction(
        config, "u1", "whatsapp", "summarize and flag anything urgent",
    )
    assert result["job_id"] == job_id
    assert result["created"] is False
    job = await get_job(config, "u1", job_id)
    ctx = json.loads(job["context"])
    assert ctx["auto_reply"] == "summarize and flag anything urgent"
    # other context fields survive the update
    assert ctx["service"] == "whatsapp"
    assert ctx["tool_name"] == "whatsapp_read"


async def test_set_creates_watcher_when_none_exists(config):
    result = await ci.set_channel_instruction(
        config, "u1", "whatsapp", "auto-ack new messages",
    )
    assert result["created"] is True
    job = await get_job(config, "u1", result["job_id"])
    assert job is not None
    assert job["job_type"] == "watcher"
    ctx = json.loads(job["context"])
    assert ctx["service"] == "whatsapp"
    assert ctx["auto_reply"] == "auto-ack new messages"
    # standing instruction → indefinite watcher
    assert ctx.get("expires_at") is None


async def test_get_round_trips(config):
    await ci.set_channel_instruction(config, "u1", "whatsapp", "be helpful")
    got = await ci.get_channel_instruction(config, "u1", "whatsapp")
    assert got is not None
    assert got["instruction"] == "be helpful"
    assert got["channel"] == "whatsapp"


async def test_clear_removes_instruction_keeps_watcher(config):
    job_id = await _seed_watcher(config, auto_reply="old instruction")
    ok = await ci.clear_channel_instruction(config, "u1", "whatsapp")
    assert ok is True
    job = await get_job(config, "u1", job_id)
    assert job is not None  # watcher survives
    ctx = json.loads(job["context"])
    assert not ctx.get("auto_reply")
    assert await ci.get_channel_instruction(config, "u1", "whatsapp") is None


async def test_clear_when_nothing_set_returns_false(config):
    assert await ci.clear_channel_instruction(config, "u1", "whatsapp") is False


async def test_unsupported_channel_raises(config):
    with pytest.raises(ValueError, match="telegram"):
        await ci.set_channel_instruction(config, "u1", "telegram", "x")


async def test_channel_wide_watcher_preferred_over_contact_watcher(config):
    # contact-specific watcher first, then a channel-wide one
    await _seed_watcher(config, tool_args={"limit": 10, "contact": "Maria"})
    wide_id = await _seed_watcher(config, tool_args={"limit": 10})
    result = await ci.set_channel_instruction(config, "u1", "whatsapp", "i")
    assert result["job_id"] == wide_id


async def test_instructions_isolated_per_channel(config):
    await ci.set_channel_instruction(config, "u1", "whatsapp", "wa instruction")
    await ci.set_channel_instruction(config, "u1", "email", "mail instruction")
    wa = await ci.get_channel_instruction(config, "u1", "whatsapp")
    em = await ci.get_channel_instruction(config, "u1", "email")
    assert wa["instruction"] == "wa instruction"
    assert em["instruction"] == "mail instruction"
    jobs = await list_jobs(config, "u1")
    assert len(jobs) == 2
