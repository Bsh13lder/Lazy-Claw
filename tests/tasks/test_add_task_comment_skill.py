"""Test AddTaskCommentSkill — add agent comments to task threads."""

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

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


async def test_skill_adds_agent_comment(cfg, monkeypatch) -> None:
    """Test that AddTaskCommentSkill adds a comment with author='agent' and fires
    a quiet notification (telegram=False, silent=True)."""
    from lazyclaw.skills.builtin.task_manager import AddTaskCommentSkill
    from lazyclaw.tasks import store as task_store

    sent = []

    async def fake_notify(*a, **k):
        sent.append(k)

    monkeypatch.setattr("lazyclaw.notifications.spine.notify", fake_notify)

    task = await task_store.create_task(cfg, "u1", "buy paint")
    skill = AddTaskCommentSkill(config=cfg)
    out = await skill.execute("u1", {"task_name": "paint", "text": "found 2 shops"})
    assert "buy paint" in out

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    comments = task_store.decode_comments(fetched["comments"])
    assert comments[-1]["author"] == "agent"
    assert comments[-1]["text"] == "found 2 shops"
    assert sent and sent[0]["telegram"] is False and sent[0]["silent"] is True
