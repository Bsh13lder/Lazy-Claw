"""Regression tests for two task-store bugs.

T1 — ``update_task`` used to encrypt a list-shaped ``tags`` value directly.
``tags`` is in ``ENCRYPTED_FIELDS`` and arrives as a Python ``list`` (from
``UpdateTaskBody.tags``), so ``encrypt(value, key)`` hit ``list.encode`` →
``AttributeError`` → HTTP 500, and the tag edit was silently lost.
``create_task`` did it correctly (``json.dumps(tags)`` first); ``update_task``
did not.

T3 — ``complete_task`` had no status guard, so a second ``/complete`` on an
already-done RECURRING task re-ran the recurring-respawn block and called
``create_task`` again, spawning a DUPLICATE next occurrence. Mobile sync is
at-least-once (a dropped response re-pushes the complete op) and a web
double-click both trigger the second call.

Both tests are store-level and run against an isolated temp DB (never the
live ``./data``) — same DB-isolation pattern as the sibling suite.
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# T1 — editing tags must round-trip (not 500 and drop the tags)
# ---------------------------------------------------------------------------

async def test_update_task_tags_round_trips(cfg) -> None:
    """Updating ``tags`` with a Python list must persist and read back."""
    task = await task_store.create_task(cfg, "u1", "tag me")

    ok = await task_store.update_task(cfg, "u1", task["id"], tags=["a", "b"])
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    # The store persists tags as an encrypted JSON string; decrypt+parse it.
    assert json.loads(fetched["tags"]) == ["a", "b"]


async def test_update_task_tags_replaces_existing(cfg) -> None:
    """A tags edit fully replaces the prior list (no merge, no loss)."""
    task = await task_store.create_task(cfg, "u1", "retag", tags=["old"])

    ok = await task_store.update_task(cfg, "u1", task["id"], tags=["new1", "new2"])
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    assert json.loads(fetched["tags"]) == ["new1", "new2"]


# ---------------------------------------------------------------------------
# T3 — completing a recurring task twice must be idempotent (no duplicate)
# ---------------------------------------------------------------------------

async def test_complete_recurring_task_is_idempotent(cfg) -> None:
    """A second ``complete_task`` on an already-done recurring task must NOT
    spawn a second next occurrence."""
    task = await task_store.create_task(
        cfg, "u1", "daily standup", recurring="0 9 * * *"
    )

    first = await task_store.complete_task(cfg, "u1", task["id"])
    assert first is True
    after_first = await task_store.list_tasks(cfg, "u1")
    # Original (now done) + exactly one freshly-spawned next occurrence.
    assert len(after_first) == 2

    # At-least-once retry / double-click: complete the same (already-done) task.
    second = await task_store.complete_task(cfg, "u1", task["id"])
    assert second is True  # return contract unchanged: still reports success
    after_second = await task_store.list_tasks(cfg, "u1")
    assert len(after_second) == 2, (
        "second complete on an already-done recurring task duplicated the "
        "next occurrence"
    )


async def test_complete_non_recurring_task_still_works(cfg) -> None:
    """The idempotency guard must not change non-recurring completion."""
    task = await task_store.create_task(cfg, "u1", "one-off")

    ok = await task_store.complete_task(cfg, "u1", task["id"])
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    assert fetched["status"] == "done"
    assert fetched["completed_at"] is not None
    # No respawn for a non-recurring task.
    assert len(await task_store.list_tasks(cfg, "u1")) == 1
