"""SQLite FTS5 helpers — title-level BM25 for LazyBrain hybrid search.

FTS5 ships in stock SQLite (no extra deps). The ``notes_fts`` virtual
table is created in :mod:`lazyclaw.db.connection.init_db` (Phase A); here
we only manage write-through and ranked retrieval.

Threat model: we index ``title_key`` only — that field is *already*
plaintext in the schema (required for ``[[wikilink]]`` resolution, see
``store.py:133``), so this module adds zero new plaintext leak. Chunk-level
content FTS lives in :mod:`lazyclaw.lazybrain.chunker` /
``note_chunks_fts`` and IS a deliberate plaintext expansion for BM25 over
content — disable that table on a hostile host.

Failures are logged at debug and never raised: hybrid retrieval treats an
empty FTS list as "BM25 path unavailable" and falls through to dense-only.
"""
from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


def _safe_match(query: str) -> str:
    """Build an FTS5 MATCH expression from a free-text query.

    Each whitespace-split token is wrapped in double quotes so FTS5
    treats it as a literal phrase. Without this, a colon inside a
    token (e.g. ``topic:foo``) makes FTS5 parse the prefix as a column
    name and raise ``no such column: topic`` against indexes that only
    have ``title`` / ``chunk_text``. Embedded quotes are dropped — they
    can't appear inside an FTS5 phrase token anyway.
    """
    cleaned = (query or "").replace('"', " ")
    tokens = [tok for tok in cleaned.split() if tok]
    return " ".join(f'"{tok}"' for tok in tokens)


async def upsert_title(
    config: Config, user_id: str, note_id: str, title_key: str | None
) -> None:
    """Refresh the ``notes_fts`` row for one note. Best-effort."""
    if not title_key:
        return
    try:
        async with db_session(config) as db:
            # Manual replace — FTS5 doesn't support ON CONFLICT, so we
            # delete by note_id then insert.
            await db.execute(
                "DELETE FROM notes_fts WHERE note_id = ?",
                (note_id,),
            )
            await db.execute(
                "INSERT INTO notes_fts (note_id, user_id, title) VALUES (?, ?, ?)",
                (note_id, user_id, title_key),
            )
            await db.commit()
    except Exception:
        logger.debug("notes_fts upsert failed", exc_info=True)


async def delete(config: Config, note_id: str) -> None:
    """Remove a note from notes_fts. Best-effort."""
    try:
        async with db_session(config) as db:
            await db.execute(
                "DELETE FROM notes_fts WHERE note_id = ?",
                (note_id,),
            )
            await db.commit()
    except Exception:
        logger.debug("notes_fts delete failed", exc_info=True)


async def search_titles(
    config: Config, user_id: str, query: str, *, limit: int = 50
) -> list[str]:
    """Return note_ids ranked by BM25 over the title FTS index.

    Output is best-first; consumers should treat list position as rank for
    RRF fusion. Uses SQLite's built-in ``bm25()`` ranking function with
    default weights — title-only index, so weights don't matter much.

    Returns ``[]`` when the index is missing, the query is empty, or any
    FTS error is raised (matches the never-raise contract).
    """
    q = (query or "").strip()
    if not q:
        return []
    # Wrap each token as a double-quoted FTS5 phrase. Bare tokens trigger
    # FTS5's `column:term` parser, so a query like ``topic:foo`` raises
    # ``no such column: topic`` against this title-only index. Quoting
    # neutralises colons and every other special char in one stroke.
    fts_query = _safe_match(q)
    if not fts_query:
        return []
    try:
        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT note_id FROM notes_fts "
                "WHERE notes_fts MATCH ? AND user_id = ? "
                "ORDER BY bm25(notes_fts) "  # ASC: lower bm25 = better match
                "LIMIT ?",
                (fts_query, user_id, max(1, min(500, limit))),
            )
            data = await rows.fetchall()
        return [r[0] for r in data if r and r[0]]
    except Exception:
        logger.debug("notes_fts search failed", exc_info=True)
        return []


async def upsert_chunk(
    config: Config,
    user_id: str,
    note_id: str,
    chunk_idx: int,
    chunk_text: str,
) -> None:
    """Refresh one chunk's row in ``note_chunks_fts``. Best-effort."""
    if not chunk_text:
        return
    try:
        async with db_session(config) as db:
            await db.execute(
                "DELETE FROM note_chunks_fts "
                "WHERE note_id = ? AND chunk_idx = ?",
                (note_id, chunk_idx),
            )
            await db.execute(
                "INSERT INTO note_chunks_fts "
                "(note_id, user_id, chunk_idx, chunk_text) "
                "VALUES (?, ?, ?, ?)",
                (note_id, user_id, chunk_idx, chunk_text),
            )
            await db.commit()
    except Exception:
        logger.debug("note_chunks_fts upsert failed", exc_info=True)


async def delete_chunks(config: Config, note_id: str) -> None:
    """Remove every chunk for a note from note_chunks_fts. Best-effort."""
    try:
        async with db_session(config) as db:
            await db.execute(
                "DELETE FROM note_chunks_fts WHERE note_id = ?", (note_id,)
            )
            await db.commit()
    except Exception:
        logger.debug("note_chunks_fts bulk delete failed", exc_info=True)


async def search_chunks(
    config: Config, user_id: str, query: str, *, limit: int = 100
) -> list[tuple[str, int]]:
    """Return ``[(note_id, chunk_idx)]`` ranked by BM25 over chunk content.

    Best-first; collapses to deduped note ids in the caller (one note can
    have many matching chunks — keep the best-ranked chunk per note).
    """
    q = (query or "").strip()
    if not q:
        return []
    fts_query = _safe_match(q)
    if not fts_query:
        return []
    try:
        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT note_id, chunk_idx FROM note_chunks_fts "
                "WHERE note_chunks_fts MATCH ? AND user_id = ? "
                "ORDER BY bm25(note_chunks_fts) "
                "LIMIT ?",
                (fts_query, user_id, max(1, min(500, limit))),
            )
            data = await rows.fetchall()
        return [(r[0], int(r[1])) for r in data if r and r[0] is not None]
    except Exception:
        logger.debug("note_chunks_fts search failed", exc_info=True)
        return []


async def rebuild_for_user(config: Config, user_id: str) -> dict:
    """One-shot: clear & rewrite ``notes_fts`` rows for one user.

    Used by the ``lazybrain_rebuild_fts`` skill to backfill an existing
    vault after the FTS5 migration first lands. Chunk-level FTS is
    rebuilt separately via the chunk reindex daemon.
    """
    written = 0
    skipped = 0
    try:
        async with db_session(config) as db:
            await db.execute(
                "DELETE FROM notes_fts WHERE user_id = ?", (user_id,)
            )
            rows = await db.execute(
                "SELECT id, title_key FROM notes WHERE user_id = ?",
                (user_id,),
            )
            data = await rows.fetchall()
            for note_id, title_key in data:
                if not title_key:
                    skipped += 1
                    continue
                await db.execute(
                    "INSERT INTO notes_fts (note_id, user_id, title) "
                    "VALUES (?, ?, ?)",
                    (note_id, user_id, title_key),
                )
                written += 1
            await db.commit()
    except Exception as exc:
        logger.warning("notes_fts rebuild failed: %s", exc)
        return {"written": written, "skipped": skipped, "error": str(exc)}
    return {"written": written, "skipped": skipped}


__all__ = [
    "upsert_title",
    "delete",
    "search_titles",
    "upsert_chunk",
    "delete_chunks",
    "search_chunks",
    "rebuild_for_user",
]
