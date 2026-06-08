"""Regression: delete_note soft-delete semantics and visibility.

delete_note now uses a soft-delete (sets deleted_at) instead of a hard DELETE.
The row is preserved so the offline-sync /api/lazybrain/notes/changes delta feed
can inform clients of deletes. Dependent rows (note_embeddings, note_chunks,
note_links) are intentionally NOT swept on soft-delete -- the note still logically
exists for the delta feed. A future hard-purge GC path would sweep those after a
retention window.

Prior to feat/flutter-mobile this was a hard delete that swept all dependent rows.
The sweep is now deferred to a separate hard-purge path (not yet implemented).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store


@pytest.fixture
async def del_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-del", "deluser", "x", "salt-del-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


async def _count(cfg: Config, sql: str, *args) -> int:
    async with db_session(cfg) as db:
        cur = await db.execute(sql, args)
        return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_delete_note_soft_deletes_and_hides_from_reads(del_config) -> None:
    """delete_note sets deleted_at, hides from get_note/list_notes, preserves row."""
    cfg = del_config
    b = await store.save_note(cfg, "u-del", content="beta body", title="Beta")
    a = await store.save_note(cfg, "u-del", content="links to [[Beta]]", title="Alpha")
    # Gamma -> Alpha (inbound link to the note we will delete).
    await store.save_note(cfg, "u-del", content="refers to [[Alpha]]", title="Gamma")
    aid = a["id"]

    # Seed an embedding + a chunk row for Alpha.
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO note_embeddings (note_id, user_id, model, dim, vector) "
            "VALUES (?, ?, ?, ?, ?)",
            (aid, "u-del", "test-model", 3, "enc:fake"),
        )
        # note_chunks has 6 required columns: note_id, user_id, chunk_idx, model, dim, vector
        await db.execute(
            "INSERT INTO note_chunks (note_id, user_id, chunk_idx, model, dim, vector) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (aid, "u-del", 0, "test-model", 3, "enc:fake"),
        )
        await db.commit()

    # Sanity: Alpha has outbound and inbound links.
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE from_note_id = ?", aid) >= 1
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE to_note_id = ?", aid) >= 1

    result = await store.delete_note(cfg, "u-del", aid)
    assert result is True

    # Note is hidden from normal read paths (deleted_at IS NULL filter).
    assert await store.get_note(cfg, "u-del", aid) is None

    # Row still exists in DB with deleted_at set (soft-delete tombstone).
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM notes WHERE id = ?", (aid,)
        )
        row = await cur.fetchone()
    assert row is not None, "soft-deleted note row must still exist in DB"
    assert row[0] is not None, "deleted_at must be set after soft-delete"

    # Dependent rows are preserved (NOT swept -- soft-delete keeps the note
    # logically alive for the delta feed; a future hard-purge would sweep them).
    assert await _count(cfg, "SELECT COUNT(*) FROM note_embeddings WHERE note_id = ?", aid) == 1
    assert await _count(cfg, "SELECT COUNT(*) FROM note_chunks WHERE note_id = ?", aid) == 1
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE from_note_id = ?", aid) >= 1

    # Beta survived (delete was scoped to Alpha).
    assert await store.get_note(cfg, "u-del", b["id"]) is not None
