"""Comment-thread storage on the tasks row (encrypted JSON list column)."""
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


async def test_comments_column_exists_and_defaults_null(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "a task")
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert "comments" in fetched
    assert fetched["comments"] is None


async def test_update_task_rejects_comments_field(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "guarded")
    with pytest.raises(ValueError):
        await task_store.update_task(cfg, "u1", task["id"], comments="[]")


async def test_add_comment_appends_and_round_trips(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "commented")
    entry = await task_store.add_comment(
        cfg, "u1", task["id"], text="first comment", author="user",
    )
    assert entry["id"].startswith("c-")
    assert entry["author"] == "user"
    assert entry["subtask_id"] is None

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    parsed = task_store.decode_comments(fetched["comments"])
    assert [c["text"] for c in parsed] == ["first comment"]
    # Encrypted at rest: the raw column must be an enc:v1 envelope.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT comments FROM tasks WHERE id = ?", (task["id"],)
        )
        raw = (await cur.fetchone())[0]
    assert raw.startswith("enc:v1:")


async def test_add_comment_bumps_updated_at_for_sync(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "sync me")
    before = (await task_store.get_task(cfg, "u1", task["id"]))["updated_at"]
    await task_store.add_comment(cfg, "u1", task["id"], text="hi")
    after = (await task_store.get_task(cfg, "u1", task["id"]))["updated_at"]
    assert after > before


async def test_add_comment_validates_subtask_id(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "with steps", steps=["step one"])
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    step_id = task_store.decode_steps(fetched["steps"])[0]["id"]

    ok = await task_store.add_comment(
        cfg, "u1", task["id"], text="on the step", subtask_id=step_id,
    )
    assert ok["subtask_id"] == step_id
    with pytest.raises(ValueError):
        await task_store.add_comment(
            cfg, "u1", task["id"], text="bad", subtask_id="s-nope",
        )


async def test_add_comment_idempotent_on_client_id(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "replayed")
    a = await task_store.add_comment(
        cfg, "u1", task["id"], text="once", comment_id="c-client1",
    )
    b = await task_store.add_comment(
        cfg, "u1", task["id"], text="once", comment_id="c-client1",
    )
    assert a["id"] == b["id"] == "c-client1"
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert len(task_store.decode_comments(fetched["comments"])) == 1


async def test_comment_cap_raises(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "capped")
    key_patch = task_store.MAX_COMMENTS_PER_TASK
    # Seed to the cap via the public API-shape (write the column directly to
    # keep the test fast), then assert the 501st append refuses.
    from lazyclaw.crypto.encryption import encrypt
    from lazyclaw.crypto.key_manager import get_user_dek
    key = await get_user_dek(cfg, "u1")
    filler = [
        {"id": f"c-{i}", "ts": "2026-01-01T00:00:00+00:00",
         "author": "user", "text": "x", "subtask_id": None}
        for i in range(key_patch)
    ]
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET comments = ? WHERE id = ?",
            (encrypt(json.dumps(filler), key), task["id"]),
        )
        await db.commit()
    with pytest.raises(task_store.CommentLimitReached):
        await task_store.add_comment(cfg, "u1", task["id"], text="overflow")


async def test_delete_comment(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "deletable")
    entry = await task_store.add_comment(cfg, "u1", task["id"], text="bye")
    assert await task_store.delete_comment(cfg, "u1", task["id"], entry["id"]) is True
    assert await task_store.delete_comment(cfg, "u1", task["id"], entry["id"]) is False
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched["comments"] is None  # empty list clears the column (steps convention)
    assert await task_store.delete_comment(cfg, "u1", "missing-task", "c-x") is None


async def test_changes_feed_carries_decrypted_comments(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "feed me")
    await task_store.add_comment(cfg, "u1", task["id"], text="in the feed")
    feed = await task_store.get_task_changes(cfg, "u1", None)
    row = next(t for t in feed["tasks"] if t["id"] == task["id"])
    assert [c["text"] for c in task_store.decode_comments(row["comments"])] == ["in the feed"]


async def test_add_comment_validates_text_and_author(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "validated")
    with pytest.raises(ValueError):
        await task_store.add_comment(cfg, "u1", task["id"], text="   ")
    with pytest.raises(ValueError):
        await task_store.add_comment(
            cfg, "u1", task["id"], text="x" * (task_store.MAX_COMMENT_CHARS + 1),
        )
    with pytest.raises(ValueError):
        await task_store.add_comment(
            cfg, "u1", task["id"], text="ok", author="martian",
        )
    # none of the rejected attempts may have persisted anything
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched["comments"] is None
