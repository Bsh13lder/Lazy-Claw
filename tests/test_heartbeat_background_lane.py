"""Heartbeat → background lane routing.

Verifies cron-tick / reminder-fire / watcher-fire all enqueue on a
separate ``:heartbeat`` lane key, never on the user's foreground lane.

Without this separation a cron tick blocks the user's next chat
message until the cron's brain turn completes (under MODE_CLAUDE that
is 30-120s — the Web UI looks frozen).

Repros the 2026-05-16 complaint: "even watcher cron jobs is going on
foreground wtf is happening there." The fix is to use a per-user
``:heartbeat``-suffixed lane key for every daemon-originated enqueue
so daemon work runs in parallel with foreground chat on the SAME user.

LaneQueue is keyed by the user_id string (lazyclaw/queue/lane.py:30) —
two different keys produce two independent FIFO processors that share
nothing. ``f"{user_id}:heartbeat"`` and ``user_id`` therefore never
contend.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.heartbeat.daemon import HeartbeatDaemon


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def config(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path, server_secret="test-server-secret")
    await init_db(cfg)
    return cfg


@pytest.fixture
async def seeded_user(config: Config) -> tuple[str, bytes]:
    """Insert a minimal user row so FK-constrained inserts succeed."""
    user_id = "test-user-hb-1"
    key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (user_id, "test-user", "x", "salt"),
        )
        await db.commit()
    return user_id, key


class RecordingLaneQueue:
    """Captures (lane_key, message) for every enqueue. Pretends to run."""

    def __init__(self) -> None:
        self.captured: list[tuple[str, str]] = []
        self._running = True

    async def enqueue(self, user_id: str, message: str, **kwargs: Any) -> str:
        self.captured.append((user_id, message))
        return ""  # empty success result — no mark_run_outcome triggers


# ── Tests ─────────────────────────────────────────────────────────────


async def test_cron_job_routes_to_heartbeat_lane_not_foreground(
    config: Config, seeded_user: tuple[str, bytes]
) -> None:
    """A due cron job must enqueue on ``{user_id}:heartbeat`` so it
    runs in parallel with the user's foreground chat lane.
    """
    user_id, key = seeded_user

    # Insert a due cron job (next_run in 1970 → always due)
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO agent_jobs (id, user_id, name, job_type, "
            "instruction, cron_expression, status, next_run) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "job-cron-1",
                user_id,
                encrypt("test-cron", key),
                "cron",
                encrypt("do thing", key),
                "*/5 * * * *",
                "active",
                "1970-01-01T00:00:00+00:00",
            ),
        )
        await db.commit()

    lane = RecordingLaneQueue()
    daemon = HeartbeatDaemon(config=config, lane_queue=lane)
    await daemon._check_due_jobs(user_id)

    assert lane.captured, "Expected the due cron job to be enqueued"
    for lane_key, _msg in lane.captured:
        assert lane_key == f"{user_id}:heartbeat", (
            f"Cron must route to heartbeat lane; got {lane_key!r}. "
            f"Raw user_id keys block the user's foreground chat."
        )


async def test_reminder_routes_to_heartbeat_lane_not_foreground(
    config: Config, seeded_user: tuple[str, bytes]
) -> None:
    """One-shot reminders fire from heartbeat too — must not block chat."""
    user_id, key = seeded_user

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO agent_jobs (id, user_id, name, job_type, "
            "instruction, status, next_run) VALUES "
            "(?, ?, ?, ?, ?, ?, ?)",
            (
                "job-reminder-1",
                user_id,
                encrypt("test-reminder", key),
                "reminder",
                encrypt("call mom", key),
                "active",
                "1970-01-01T00:00:00+00:00",
            ),
        )
        await db.commit()

    lane = RecordingLaneQueue()
    daemon = HeartbeatDaemon(config=config, lane_queue=lane)
    await daemon._check_due_reminders(user_id, key)

    assert lane.captured, "Expected the due reminder to be enqueued"
    for lane_key, _msg in lane.captured:
        assert lane_key == f"{user_id}:heartbeat", (
            f"Reminder must route to heartbeat lane; got {lane_key!r}"
        )


async def test_heartbeat_lane_does_not_contend_with_foreground_lane(
    config: Config, seeded_user: tuple[str, bytes]
) -> None:
    """Concurrent foreground + heartbeat enqueues must NOT serialize.

    Uses a real LaneQueue. A slow handler on the heartbeat lane should
    not delay a fast handler running for the same user on the
    foreground lane. If both keys collapse to the same lane this test
    times out / orders the results sequentially.
    """
    from lazyclaw.queue.lane import LaneQueue

    user_id, _key = seeded_user
    order: list[str] = []

    async def handler(uid: str, msg: str, **_kw: Any) -> str:
        if "slow" in msg:
            await asyncio.sleep(0.20)
            order.append("slow")
        else:
            order.append("fast")
        return msg

    queue = LaneQueue()
    queue.set_handler(handler)
    await queue.start()
    try:
        # Fire slow heartbeat enqueue, then fast foreground enqueue
        slow_task = asyncio.create_task(
            queue.enqueue(f"{user_id}:heartbeat", "slow-heartbeat")
        )
        await asyncio.sleep(0.01)  # ensure slow lands first
        fast_task = asyncio.create_task(queue.enqueue(user_id, "fast-foreground"))
        await asyncio.wait_for(asyncio.gather(slow_task, fast_task), timeout=2.0)
    finally:
        await queue.stop()

    # Foreground (fast) must complete BEFORE heartbeat (slow) — they
    # ran in parallel on independent lanes. If the test environment
    # serialized them onto one lane, order would be ["slow", "fast"].
    assert order == ["fast", "slow"], (
        f"Heartbeat and foreground lanes contended (order={order}); "
        "fix should route daemon work to a separate per-user lane key."
    )
