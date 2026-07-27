"""Task-linked reminder jobs must not accumulate as permanent zombies.

Found while chasing "the notification log is noisy" (2026-07-27). In the live
DB: 5 active reminder jobs, **every one with `last_run` NULL**, and 3 of them
with a `next_run` already in the past — one owned by a task that is already
`done`, two days stale.

Mechanism — `daemon._check_due_reminders`:

    if message and "[TASK_REMINDER:" in message:
        continue

The `continue` skips the job WITHOUT deleting it and WITHOUT advancing
`next_run` (task reminders are fired from the tasks table by
`_check_task_nagging`, not from this job row). The row therefore stays
`status='active'` with a past `next_run` forever, so the query
`... AND next_run <= now` re-selects it on EVERY heartbeat tick and pays a
decrypt for it, permanently.

The job row exists only as a handle for `complete_task`/`update_task` to cancel
the reminder. Once no live open task references it, it is garbage — and it is
unreachable garbage, because those are the only code paths that delete it.

`tasks.reminder_job_id` is a plaintext column, so the orphan set is a plain
anti-join; no decryption needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


async def _active_reminder_job_ids(cfg: Config) -> set[str]:
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id FROM agent_jobs WHERE job_type = 'reminder' "
            "AND status = 'active'"
        )
        return {r[0] for r in await cur.fetchall()}


async def _orphan(cfg: Config) -> str:
    """A stale task-linked reminder job whose owning task is gone."""
    job_id = await task_store._create_reminder_job(
        cfg, "u1", "ghost task", _iso(days=-2), "task-that-no-longer-exists",
    )
    # Age the row past the safety grace window.
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE agent_jobs SET created_at = ? WHERE id = ?",
            (_iso(days=-2), job_id),
        )
        await db.commit()
    return job_id


async def test_orphaned_reminder_job_is_reaped(cfg) -> None:
    """A past-due reminder job no live task references must be deleted."""
    job_id = await _orphan(cfg)
    assert job_id in await _active_reminder_job_ids(cfg)

    reaped = await task_store.reap_orphan_reminder_jobs(cfg, "u1")

    assert reaped == 1
    assert job_id not in await _active_reminder_job_ids(cfg), (
        "the orphaned reminder job survived — it will be re-selected and "
        "decrypted on every heartbeat tick, forever"
    )


async def test_job_of_a_live_open_task_is_kept(cfg) -> None:
    """The reap must never touch a reminder a live task still depends on."""
    task = await task_store.create_task(
        cfg, "u1", "real task", reminder_at=_iso(days=-1),
    )
    job_id = task["reminder_job_id"]
    assert job_id
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE agent_jobs SET created_at = ? WHERE id = ?",
            (_iso(days=-2), job_id),
        )
        await db.commit()

    reaped = await task_store.reap_orphan_reminder_jobs(cfg, "u1")

    assert reaped == 0
    assert job_id in await _active_reminder_job_ids(cfg), (
        "reaped the reminder job of a live, still-open task"
    )


async def test_completed_tasks_job_is_reaped(cfg) -> None:
    """Completing a task normally deletes its job; if any path missed it (the
    PATCH status=done bypass did exactly that), the sweep is the backstop."""
    task = await task_store.create_task(
        cfg, "u1", "finished task", reminder_at=_iso(days=-1),
    )
    job_id = task["reminder_job_id"]
    async with db_session(cfg) as db:
        # Simulate the bypass: flip status without going through complete_task,
        # so the job is left behind still pointing at the row.
        await db.execute(
            "UPDATE tasks SET status = 'done' WHERE id = ?", (task["id"],),
        )
        await db.execute(
            "UPDATE agent_jobs SET created_at = ? WHERE id = ?",
            (_iso(days=-2), job_id),
        )
        await db.commit()

    reaped = await task_store.reap_orphan_reminder_jobs(cfg, "u1")

    assert reaped == 1
    assert job_id not in await _active_reminder_job_ids(cfg)


async def test_freshly_created_job_is_never_reaped(cfg) -> None:
    """Grace window: ``_create_reminder_job`` runs BEFORE the task row that
    references it is inserted, so a sweep firing inside that gap must not
    delete a perfectly good job.
    """
    job_id = await task_store._create_reminder_job(
        cfg, "u1", "just created", _iso(days=-1), "task-not-inserted-yet",
    )

    reaped = await task_store.reap_orphan_reminder_jobs(cfg, "u1")

    assert reaped == 0
    assert job_id in await _active_reminder_job_ids(cfg), (
        "reaped a job created seconds ago — this races task creation"
    )


async def test_future_job_is_never_reaped(cfg) -> None:
    """Only PAST-DUE jobs are candidates; a scheduled future reminder stays."""
    job_id = await task_store._create_reminder_job(
        cfg, "u1", "next week", _iso(days=7), "orphan-but-future",
    )
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE agent_jobs SET created_at = ? WHERE id = ?",
            (_iso(days=-2), job_id),
        )
        await db.commit()

    reaped = await task_store.reap_orphan_reminder_jobs(cfg, "u1")

    assert reaped == 0
    assert job_id in await _active_reminder_job_ids(cfg)
