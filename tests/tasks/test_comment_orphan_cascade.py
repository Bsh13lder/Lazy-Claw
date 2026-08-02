"""Deleting a subtask must not orphan its comments (set_steps cascade)."""
from __future__ import annotations

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


async def test_set_steps_prunes_comments_on_removed_subtask(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "with steps", steps=["A", "B"])
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    steps = task_store.decode_steps(fetched["steps"])
    step_a, step_b = steps[0], steps[1]

    comment_a = await task_store.add_comment(
        cfg, "u1", task["id"], text="on A", subtask_id=step_a["id"],
    )
    comment_b = await task_store.add_comment(
        cfg, "u1", task["id"], text="on B", subtask_id=step_b["id"],
    )
    comment_task = await task_store.add_comment(
        cfg, "u1", task["id"], text="task-level",
    )

    before = await task_store.get_task(cfg, "u1", task["id"])
    updated_at_before = before["updated_at"]

    # Replace the checklist keeping only step B (simulates deleting A).
    result = await task_store.set_steps(cfg, "u1", task["id"], [step_b])
    assert [s["id"] for s in result] == [step_b["id"]]

    after = await task_store.get_task(cfg, "u1", task["id"])
    remaining_ids = {c["id"] for c in task_store.decode_comments(after["comments"])}

    assert comment_a["id"] not in remaining_ids
    assert comment_b["id"] in remaining_ids
    assert comment_task["id"] in remaining_ids
    assert after["updated_at"] > updated_at_before


async def test_set_steps_leaves_comments_untouched_when_nothing_orphaned(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "with steps", steps=["A"])
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    step_a = task_store.decode_steps(fetched["steps"])[0]

    await task_store.add_comment(
        cfg, "u1", task["id"], text="on A", subtask_id=step_a["id"],
    )
    await task_store.add_comment(cfg, "u1", task["id"], text="task-level")

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT comments FROM tasks WHERE id = ?", (task["id"],)
        )
        raw_before = (await cur.fetchone())[0]

    # Re-set the SAME step (e.g. a title edit) — nothing is orphaned.
    await task_store.set_steps(cfg, "u1", task["id"], [step_a])

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT comments FROM tasks WHERE id = ?", (task["id"],)
        )
        raw_after = (await cur.fetchone())[0]

    # Byte-identical: the comments column must not be rewritten (no churn,
    # different AES-GCM nonce every write would otherwise always differ).
    assert raw_after == raw_before
