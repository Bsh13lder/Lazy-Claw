"""Regression: delete_note must sweep EVERY dependent row.

PRAGMA foreign_keys is OFF in this deployment, so no ON DELETE CASCADE
fires. delete_note previously left orphaned embeddings, chunk rows, and
outbound note_links — orphaned dense/BM25 hits surfaced deleted notes and
phantom graph edges dangled from dead source nodes (observed live on
2026-05-29: 390 orphan index rows + 243 orphan note_links).
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
async def test_delete_note_sweeps_all_dependent_rows(del_config) -> None:
    cfg = del_config
    b = await store.save_note(cfg, "u-del", content="beta body", title="Beta")
    a = await store.save_note(cfg, "u-del", content="links to [[Beta]]", title="Alpha")
    # Gamma -> Alpha (inbound link to the note we will delete).
    await store.save_note(cfg, "u-del", content="refers to [[Alpha]]", title="Gamma")
    aid = a["id"]

    # Seed an embedding + a chunk row for Alpha (FK cascade would normally
    # remove these, but it is OFF — so delete_note must do it explicitly).
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO note_embeddings (note_id, user_id, model, dim, vector) "
            "VALUES (?, ?, ?, ?, ?)",
            (aid, "u-del", "test-model", 3, "enc:fake"),
        )
        await db.execute(
            "INSERT INTO note_chunks (note_id, user_id, chunk_idx, model, dim, vector) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (aid, "u-del", 0, "test-model", 3, "enc:fake"),
        )
        await db.commit()

    # Sanity: Alpha has an outbound link (->Beta) and an inbound link (Gamma->).
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE from_note_id = ?", aid) >= 1
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE to_note_id = ?", aid) >= 1

    assert await store.delete_note(cfg, "u-del", aid) is True

    # Note + every dependent row gone.
    assert await store.get_note(cfg, "u-del", aid) is None
    assert await _count(cfg, "SELECT COUNT(*) FROM note_embeddings WHERE note_id = ?", aid) == 0
    assert await _count(cfg, "SELECT COUNT(*) FROM note_chunks WHERE note_id = ?", aid) == 0
    # Outbound links from the dead note removed.
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE from_note_id = ?", aid) == 0
    # Inbound pointer nulled (no link still resolves to the dead note).
    assert await _count(cfg, "SELECT COUNT(*) FROM note_links WHERE to_note_id = ?", aid) == 0

    # Beta survived (delete was scoped to Alpha).
    assert await store.get_note(cfg, "u-del", b["id"]) is not None
