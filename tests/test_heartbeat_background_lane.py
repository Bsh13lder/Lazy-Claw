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
    """Captures (routing_lane_key, message) for every enqueue. Pretends to run.

    The daemon enqueues with the bare ``user_id`` as identity and a separate
    ``lane_key`` for routing, so the routing key is what we assert on here.
    """

    def __init__(self) -> None:
        self.captured: list[tuple[str, str]] = []
        self._running = True

    async def enqueue(
        self, user_id: str, message: str, *, lane_key: str | None = None, **kwargs: Any
    ) -> str:
        self.captured.append((lane_key or user_id, message))
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


async def test_due_cron_job_invokes_handler_with_bare_user_id(
    config: Config, seeded_user: tuple[str, bytes]
) -> None:
    """A daemon-fired cron job must invoke the message handler with the
    BARE ``user_id`` — never the ``{user_id}:heartbeat`` lane-routing key.

    Repros the 2026-05-16 regression (commit 33db41a): the daemon began
    enqueueing on ``f"{user_id}:heartbeat"``, but ``LaneQueue`` used that
    same string as the job's identity, so ``process_message`` passed it to
    ``get_user_dek`` → ``ValueError: User <id>:heartbeat not found``. Every
    daemon-fired brain turn (cron, watcher, MCP auto-reply) failed 100%.

    The lane key (routing) and the user_id (identity) must be decoupled.
    """
    from lazyclaw.queue.lane import LaneQueue

    user_id, key = seeded_user

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO agent_jobs (id, user_id, name, job_type, "
            "instruction, cron_expression, status, next_run) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "job-cron-identity",
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

    received_uids: list[str] = []

    async def handler(uid: str, _msg: str, **_kw: Any) -> str:
        received_uids.append(uid)
        return ""

    queue = LaneQueue()
    queue.set_handler(handler)
    await queue.start()
    daemon = HeartbeatDaemon(config=config, lane_queue=queue)
    try:
        await daemon._check_due_jobs(user_id)
        await asyncio.sleep(0.05)  # let the lane processor drain the job
    finally:
        await queue.stop()

    assert received_uids == [user_id], (
        "daemon-fired cron must call the handler with the bare user_id; "
        f"got {received_uids!r}. A ':heartbeat'-suffixed id breaks "
        "get_user_dek (User <id>:heartbeat not found)."
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
