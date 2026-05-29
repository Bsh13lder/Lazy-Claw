"""Sweep orphaned index rows whose note no longer exists.

delete_note cleans title-FTS but NOT the chunk base table or the vec0
mirror, so bulk deletions (and past raw-SQL purges) leave orphaned BM25 +
dense hits that can surface deleted notes. This only ever touches rows
whose note_id is absent from `notes` — it cannot affect a live note.

    docker exec [-e APPLY=1] -w /app lazyclaw python3 /tmp/graph_orphan_sweep.py
"""
from __future__ import annotations

import asyncio
import os

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session
from lazyclaw.lazybrain import embeddings, fts


async def _orphan_ids(db) -> set[str]:
    ids: set[str] = set()
    for tbl in ("note_chunks", "note_embeddings", "note_chunks_fts"):
        try:
            cur = await db.execute(
                f"SELECT DISTINCT note_id FROM {tbl} "
                f"WHERE note_id IS NOT NULL AND note_id NOT IN (SELECT id FROM notes)"
            )
            for r in await cur.fetchall():
                ids.add(r[0])
        except Exception as e:  # noqa: BLE001
            print(f"  (scan {tbl} failed: {e})")
    return ids


async def main() -> None:
    apply = os.environ.get("APPLY") == "1"
    cfg = Config()
    async with db_session(cfg) as db:
        orphans = await _orphan_ids(db)
    print(f"orphan note_ids found: {len(orphans)}")
    if not apply:
        print("DRY-RUN (set APPLY=1 to sweep). Nothing changed.")
        await close_pool()
        return

    for oid in orphans:
        # note_embeddings + vec0 mirror (handles the extension load)
        await embeddings.delete_embedding(cfg, oid)
        # note_chunks_fts (BM25 chunk index)
        await fts.delete_chunks(cfg, oid)
        # note_chunks base table (delete_note never cleaned this)
        async with db_session(cfg) as db:
            await db.execute("DELETE FROM note_chunks WHERE note_id = ?", (oid,))
            await db.commit()

    # Verify
    async with db_session(cfg) as db:
        remaining = await _orphan_ids(db)
        counts = {}
        for tbl in ("note_chunks", "note_embeddings", "note_chunks_fts"):
            cur = await db.execute(f"SELECT COUNT(*) FROM {tbl}")
            counts[tbl] = (await cur.fetchone())[0]
    print(f"swept. orphan note_ids remaining: {len(remaining)}")
    print(f"row counts now: {counts}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
