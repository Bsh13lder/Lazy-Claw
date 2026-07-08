"""Soft-deleted tasks must not keep firing reminders/nags.

2026-07-05 incident: a task deleted via ``delete_task`` (soft-delete —
sets ``deleted_at`` but leaves ``status='todo'``) kept getting nagged by
the heartbeat daemon because the reminder/nag queries filtered ``status``
but not ``deleted_at``. A throwaway verification task ("VERIFY-FIX-99")
nagged the user 5 times AFTER being deleted.

``get_nagging_tasks`` is the store-level query behind that behaviour; the
daemon's own reminder/pre-reminder queries carry the identical
``deleted_at IS NULL`` guard. These tests pin the store contract.
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
    c = Config(database_dir=tmp_path)
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


async def _make_nagging(cfg: Config, title: str) -> str:
    """Create a task and force it into the 'currently being nagged' state."""
    task = await task_store.create_task(cfg, "u1", title)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET reminder_at = ?, nag_count = 1 WHERE id = ?",
            (past, task["id"]),
        )
        await db.commit()
    return task["id"]


async def test_live_task_is_nagged(cfg):
    """Baseline: a live task with a due reminder + nag_count>0 IS returned."""
    task_id = await _make_nagging(cfg, "call the dentist")
    nagging = await task_store.get_nagging_tasks(cfg, "u1")
    assert task_id in {t["id"] for t in nagging}, (
        "a live task with a due reminder must be nagged"
    )


async def test_soft_deleted_task_is_not_nagged(cfg):
    """The fix: once soft-deleted, the task drops out of the nag set."""
    task_id = await _make_nagging(cfg, "VERIFY-FIX-99")

    deleted = await task_store.delete_task(cfg, "u1", task_id)
    assert deleted is True

    # Sanity: the row still exists with deleted_at set (soft-delete).
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at, status FROM tasks WHERE id = ?", (task_id,),
        )
        deleted_at, status = await cur.fetchone()
    assert deleted_at is not None, "delete_task must soft-delete (deleted_at set)"

    nagging = await task_store.get_nagging_tasks(cfg, "u1")
    assert task_id not in {t["id"] for t in nagging}, (
        "a soft-deleted task must NOT be nagged (deleted_at IS NULL guard)"
    )


async def test_soft_deleted_task_not_nagged_in_all_users_scan(cfg):
    """The user_id=None branch (cross-user 'done' resolver) also excludes it."""
    task_id = await _make_nagging(cfg, "VERIFY-FIX-99")
    await task_store.delete_task(cfg, "u1", task_id)

    nagging = await task_store.get_nagging_tasks(cfg, user_id=None)
    assert task_id not in {t["id"] for t in nagging}, (
        "cross-user nag scan must also exclude soft-deleted tasks"
    )
