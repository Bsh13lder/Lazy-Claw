"""Migration test: channel_threads table must exist with all required columns."""

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


async def test_channel_threads_table_exists(config):
    async with db_session(config) as db:
        cur = await db.execute("PRAGMA table_info(channel_threads)")
        cols = {r[1] for r in await cur.fetchall()}
    assert {"id", "user_id", "channel", "contact_handle", "contact_name",
            "last_preview", "unread_count", "last_activity", "last_seen_msg_id",
            "created_at", "updated_at", "deleted_at"} <= cols
