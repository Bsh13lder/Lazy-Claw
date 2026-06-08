"""Offline-sync primitives for the Tasks domain.

Covers four requirements:
1. updated_at present on create + bumped on every update path.
2. Client-supplied id on create is honoured; duplicate create is idempotent.
3. Soft-delete (deleted_at): hides from list/get but surfaces in /changes.
4. GET /api/tasks/changes?since=<iso>: delta filtering including tombstones.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 1. updated_at: present on create, bumped on every update path
# ---------------------------------------------------------------------------

async def test_updated_at_set_on_create(cfg):
    """Task created with updated_at == created_at."""
    task = await task_store.create_task(cfg, "u1", "buy milk")
    assert task["updated_at"] is not None, "updated_at must be set on create"
    assert task["updated_at"] == task["created_at"], (
        "on create, updated_at should equal created_at"
    )


async def test_updated_at_persisted_in_db(cfg):
    """updated_at is stored in the DB and readable back."""
    task = await task_store.create_task(cfg, "u1", "db round-trip")
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    assert fetched["updated_at"] == task["updated_at"]


async def test_updated_at_bumped_on_update_task(cfg):
    """update_task() must bump updated_at."""
    task = await task_store.create_task(cfg, "u1", "original title")
    original_ts = task["updated_at"]

    # Small sleep ensures the new timestamp differs by at least a microsecond.
    import asyncio
    await asyncio.sleep(0.01)

    await task_store.update_task(cfg, "u1", task["id"], status="in_progress")
    refreshed = await task_store.get_task(cfg, "u1", task["id"])
    assert refreshed["updated_at"] > original_ts, (
        "updated_at must advance on update_task"
    )


async def test_updated_at_bumped_on_complete_task(cfg):
    """complete_task() must bump updated_at."""
    task = await task_store.create_task(cfg, "u1", "finish me")
    original_ts = task["updated_at"]

    import asyncio
    await asyncio.sleep(0.01)

    await task_store.complete_task(cfg, "u1", task["id"])
    refreshed = await task_store.get_task(cfg, "u1", task["id"])
    assert refreshed["updated_at"] > original_ts, (
        "complete_task must bump updated_at"
    )


async def test_updated_at_bumped_on_fail_task(cfg):
    """fail_task() must bump updated_at."""
    task = await task_store.create_task(cfg, "u1", "will fail")
    original_ts = task["updated_at"]

    import asyncio
    await asyncio.sleep(0.01)

    await task_store.fail_task(cfg, "u1", task["id"], error="timeout")
    refreshed = await task_store.get_task(cfg, "u1", task["id"])
    assert refreshed["updated_at"] > original_ts, (
        "fail_task must bump updated_at"
    )


async def test_updated_at_bumped_on_set_steps(cfg):
    """set_steps() must bump updated_at."""
    task = await task_store.create_task(cfg, "u1", "multi-step task")
    original_ts = task["updated_at"]

    import asyncio
    await asyncio.sleep(0.01)

    await task_store.set_steps(
        cfg, "u1", task["id"],
        [{"id": "s1", "title": "step one", "done": False}],
    )
    refreshed = await task_store.get_task(cfg, "u1", task["id"])
    assert refreshed["updated_at"] > original_ts, (
        "set_steps must bump updated_at"
    )


async def test_updated_at_bumped_on_append_progress(cfg):
    """append_progress_entry() must bump updated_at."""
    task = await task_store.create_task(cfg, "u1", "track me")
    original_ts = task["updated_at"]

    import asyncio
    await asyncio.sleep(0.01)

    await task_store.append_progress_entry(
        cfg, "u1", task["id"], kind="working", text="making progress",
    )
    refreshed = await task_store.get_task(cfg, "u1", task["id"])
    assert refreshed["updated_at"] > original_ts, (
        "append_progress_entry must bump updated_at"
    )


# ---------------------------------------------------------------------------
# 2. Client-supplied id on create (idempotent replay)
# ---------------------------------------------------------------------------

async def test_create_task_with_client_id(cfg):
    """create_task with task_id= uses the client-supplied id."""
    client_id = "client-minted-id-abc123"
    task = await task_store.create_task(cfg, "u1", "offline task", task_id=client_id)
    assert task["id"] == client_id


async def test_create_task_with_client_id_idempotent(cfg):
    """Second create with the same id returns the existing task (no duplicate)."""
    client_id = "idem-id-xyz789"
    first = await task_store.create_task(cfg, "u1", "idempotent task", task_id=client_id)
    second = await task_store.create_task(cfg, "u1", "idempotent task", task_id=client_id)

    assert first["id"] == client_id
    assert second["id"] == client_id, "duplicate client-id must return the same task"

    # Only one row in DB.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM tasks WHERE id = ? AND user_id = 'u1'",
            (client_id,),
        )
        row = await cur.fetchone()
    assert row[0] == 1, "idempotent create must not insert duplicate rows"


async def test_create_task_without_client_id_generates_uuid(cfg):
    """Without task_id=, a fresh UUID is generated."""
    task = await task_store.create_task(cfg, "u1", "auto-id task")
    assert len(task["id"]) == 36, "auto-generated id should be a UUID"


# ---------------------------------------------------------------------------
# 3. Soft-delete: hides from normal reads, visible in changes
# ---------------------------------------------------------------------------

async def test_delete_task_sets_deleted_at(cfg):
    """delete_task sets deleted_at instead of removing the row."""
    task = await task_store.create_task(cfg, "u1", "soft delete me")
    ok = await task_store.delete_task(cfg, "u1", task["id"])
    assert ok is True

    # Row still exists in DB.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM tasks WHERE id = ? AND user_id = 'u1'",
            (task["id"],),
        )
        row = await cur.fetchone()
    assert row is not None, "row must still exist after soft-delete"
    assert row[0] is not None, "deleted_at must be set after delete_task"


async def test_get_task_returns_none_after_soft_delete(cfg):
    """get_task hides soft-deleted tasks."""
    task = await task_store.create_task(cfg, "u1", "hidden task")
    await task_store.delete_task(cfg, "u1", task["id"])
    result = await task_store.get_task(cfg, "u1", task["id"])
    assert result is None, "get_task must not return soft-deleted tasks"


async def test_list_tasks_excludes_soft_deleted(cfg):
    """list_tasks excludes soft-deleted tasks."""
    t_keep = await task_store.create_task(cfg, "u1", "keep")
    t_del = await task_store.create_task(cfg, "u1", "will be deleted")
    await task_store.delete_task(cfg, "u1", t_del["id"])

    tasks = await task_store.list_tasks(cfg, "u1")
    ids = [t["id"] for t in tasks]
    assert t_keep["id"] in ids, "non-deleted task must appear in list"
    assert t_del["id"] not in ids, "soft-deleted task must not appear in list"


async def test_delete_task_returns_false_for_missing_task(cfg):
    """delete_task returns False if the task doesn't exist (no crash)."""
    result = await task_store.delete_task(cfg, "u1", "nonexistent-id")
    assert result is False


# ---------------------------------------------------------------------------
# 4. GET /api/tasks/changes?since=<iso> — delta feed
# ---------------------------------------------------------------------------

async def test_get_changes_returns_all_when_no_since(cfg):
    """Without ?since=, all tasks (including tombstones) are returned."""
    t1 = await task_store.create_task(cfg, "u1", "task 1")
    t2 = await task_store.create_task(cfg, "u1", "task 2")
    await task_store.delete_task(cfg, "u1", t2["id"])

    result = await task_store.get_task_changes(cfg, "u1", since=None)
    task_ids = [t["id"] for t in result["tasks"]]
    assert t1["id"] in task_ids
    assert t2["id"] in result["deleted"], "soft-deleted id must appear in deleted list"
    assert "now" in result


async def test_get_changes_filters_by_since(cfg):
    """Only tasks updated after `since` are returned."""
    import asyncio

    t_before = await task_store.create_task(cfg, "u1", "before")
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    t_after = await task_store.create_task(cfg, "u1", "after")

    result = await task_store.get_task_changes(cfg, "u1", since=checkpoint)
    task_ids = [t["id"] for t in result["tasks"]]

    assert t_after["id"] in task_ids, "task created after since must appear"
    assert t_before["id"] not in task_ids, "task created before since must be excluded"


async def test_get_changes_includes_deleted_after_since(cfg):
    """Soft-deletes that happened after `since` appear in the deleted list."""
    import asyncio

    t = await task_store.create_task(cfg, "u1", "to be deleted later")
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    await task_store.delete_task(cfg, "u1", t["id"])

    result = await task_store.get_task_changes(cfg, "u1", since=checkpoint)
    assert t["id"] in result["deleted"], (
        "task soft-deleted after since must appear in deleted list"
    )


async def test_get_changes_now_field_is_valid_iso(cfg):
    """The `now` field in the response is a valid ISO datetime."""
    result = await task_store.get_task_changes(cfg, "u1", since=None)
    ts = result["now"]
    # Should parse without error.
    dt = datetime.fromisoformat(ts)
    assert dt is not None


async def test_get_changes_excludes_old_deletes_before_since(cfg):
    """Soft-deletes that happened before `since` are not in the deleted list."""
    import asyncio

    t = await task_store.create_task(cfg, "u1", "old delete")
    await task_store.delete_task(cfg, "u1", t["id"])
    await asyncio.sleep(0.02)
    checkpoint = datetime.now(timezone.utc).isoformat()

    result = await task_store.get_task_changes(cfg, "u1", since=checkpoint)
    assert t["id"] not in result["deleted"], (
        "soft-delete that predates since must NOT appear in deleted list"
    )


async def test_updated_at_in_db_is_iso_string(cfg):
    """updated_at stored in the DB is a valid ISO string (not a sentinel)."""
    task = await task_store.create_task(cfg, "u1", "check format")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT updated_at FROM tasks WHERE id = ?", (task["id"],)
        )
        row = await cur.fetchone()
    raw = row[0]
    assert raw is not None
    # Should parse without exception.
    datetime.fromisoformat(raw)
