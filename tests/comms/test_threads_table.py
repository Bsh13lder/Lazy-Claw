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
            "created_at", "updated_at", "deleted_at",
            "contact_handle_hash"} <= cols


async def test_dedup_index_uses_handle_hash_not_plaintext(config):
    """The unique dedup index must sit on the HMAC hash column — an index on
    the plaintext handle would leak PII into the index b-tree."""
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='channel_threads'"
        )
        indexes = {r["name"]: (r["sql"] or "") for r in await cur.fetchall()}
    assert "idx_channel_threads_unique" not in indexes  # old plaintext index dropped
    hash_idx = indexes.get("idx_channel_threads_unique_hash", "")
    assert "contact_handle_hash" in hash_idx
    assert "UNIQUE" in hash_idx.upper()


async def test_migration_upgrades_pre_encryption_table(tmp_path: Path):
    """init_db on a DB whose channel_threads predates handle encryption must
    add the hash column and swap the dedup index (idempotent ALTER path)."""
    import aiosqlite

    c = Config(database_dir=tmp_path / "old")
    (tmp_path / "old").mkdir()
    db_path = c.database_dir / "lazyclaw.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE channel_threads ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, channel TEXT NOT NULL, "
            "contact_handle TEXT NOT NULL, contact_name TEXT, last_preview TEXT, "
            "unread_count INTEGER NOT NULL DEFAULT 0, "
            "last_activity TEXT NOT NULL DEFAULT (datetime('now')), "
            "last_seen_msg_id TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "deleted_at TEXT)"
        )
        await db.execute(
            "CREATE UNIQUE INDEX idx_channel_threads_unique "
            "ON channel_threads(user_id, channel, contact_handle)"
        )
        await db.commit()

    await init_db(c)
    try:
        async with db_session(c) as db:
            cur = await db.execute("PRAGMA table_info(channel_threads)")
            cols = {r[1] for r in await cur.fetchall()}
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='channel_threads'"
            )
            idx_names = {r["name"] for r in await cur.fetchall()}
    finally:
        await close_pool()
    assert "contact_handle_hash" in cols
    assert "idx_channel_threads_unique" not in idx_names
    assert "idx_channel_threads_unique_hash" in idx_names
