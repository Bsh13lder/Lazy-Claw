"""Migration test: conversation_tasks table must exist with all required columns."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    try:
        yield c
    finally:
        await close_pool()


async def test_conversation_tasks_table_exists(config):
    async with db_session(config) as db:
        cur = await db.execute("PRAGMA table_info(conversation_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
    assert {"id","user_id","channel","contact_handle","contact_name","goal",
            "completion_criteria","status","transcript_json","iteration",
            "max_iterations","poll_interval","next_poll_at","created_at",
            "last_activity_at","expires_at","result","error","approval_id"} <= cols
