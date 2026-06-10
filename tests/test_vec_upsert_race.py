"""Regression: concurrent ``_vec_upsert`` for the same note.

Two coroutines upserting the same note_id interleaved on the shared
aiosqlite connection as DELETE / DELETE / INSERT / INSERT — the second
INSERT hit ``UNIQUE constraint failed on vec_note_embeddings primary
key`` (seen 5+ times in prod logs 2026-06-07..08, traceback at
embeddings.py:322). DELETE-then-INSERT alone can't survive interleaving;
``_VEC_UPSERT_LOCK`` must serialize the pair.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from lazyclaw.lazybrain import embeddings


class _FakeDB:
    """Records op order and yields control between ops to force the
    interleaving window the production race needs."""

    def __init__(self, ops: list[str]) -> None:
        self._ops = ops

    async def execute(self, sql: str, params=()) -> None:
        self._ops.append("DEL" if sql.lstrip().startswith("DELETE") else "INS")
        await asyncio.sleep(0)

    async def commit(self) -> None:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_concurrent_upserts_for_same_note_do_not_interleave():
    ops: list[str] = []

    @asynccontextmanager
    async def fake_session(config):
        yield _FakeDB(ops)

    async def vec_ok(config, db) -> bool:
        return True

    with (
        patch.object(embeddings, "db_session", fake_session),
        patch.object(embeddings, "_vec_available", vec_ok),
    ):
        vec = [0.0] * embeddings.EMBED_DIM
        config = None  # fake session ignores it
        results = await asyncio.gather(
            embeddings._vec_upsert(config, "u1", "note-1", vec),  # type: ignore[arg-type]
            embeddings._vec_upsert(config, "u1", "note-1", vec),  # type: ignore[arg-type]
        )

    assert results == [True, True]
    # Serialized: each writer's DELETE+INSERT pair stays atomic.
    # Without the lock the sleep(0) yields produce DEL, DEL, INS, INS.
    assert ops == ["DEL", "INS", "DEL", "INS"]
