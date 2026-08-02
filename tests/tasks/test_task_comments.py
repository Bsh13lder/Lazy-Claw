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
