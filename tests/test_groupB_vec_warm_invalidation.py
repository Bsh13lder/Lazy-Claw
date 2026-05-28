"""Tests for B1 — _VEC_WARMED invalidation on _vec_upsert failure.

Ensures that when _vec_upsert fails (returns False), upsert_embedding
removes the (db_path, user_id) key from _VEC_WARMED so that
_ensure_vec_mirror_warmed re-runs on the next semantic_search call and
the failing note becomes visible once the transient error clears.

Also asserts the inverse: a successful upsert does NOT remove the key
(no spurious invalidation of an already-warm state).
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# We import _vec_upsert and upsert_embedding from embeddings directly.
# The embedding module imports heavy deps (aiosqlite, etc.) so we only need
# to monkeypatch the minimal surface.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers — thin stubs so we can call upsert_embedding without a real DB
# or Ollama instance.
# ---------------------------------------------------------------------------

def _get_module():
    """Return the embeddings module, imported fresh (no side effects)."""
    import lazyclaw.lazybrain.embeddings as emb
    return emb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_vec_upsert_failure_removes_key_from_vec_warmed(tmp_path: Path):
    """If _vec_upsert returns False, upsert_embedding discards the warm key."""
    emb = _get_module()

    # Create a minimal Config whose db_path resolves deterministically.
    from lazyclaw.config import Config
    cfg = Config(database_dir=tmp_path)

    user_id = "u-b1-fail"
    note_id = "n-b1-fail"
    text = "hello world"

    # Pre-warm the key so we can observe it being removed.
    from lazyclaw.db.connection import get_db_path
    db_path = str(get_db_path(cfg))
    key = (db_path, user_id)
    emb._VEC_WARMED.add(key)
    assert key in emb._VEC_WARMED, "pre-condition: key should be in _VEC_WARMED"

    # Patch the costly internals:
    #   _ollama_embed  → return a fake vector so upsert_embedding proceeds
    #   get_user_dek   → return a fake dek bytes object
    #   db_session     → async context manager no-op (avoids real DB calls)
    #   _fts module-level functions → no-op
    #   _cache_put → no-op
    #   _vec_upsert → return False  (the failure case under test)

    fake_vec = [0.1] * 10
    fake_dek = b"A" * 32  # exactly 256-bit AES key

    @contextlib.asynccontextmanager
    async def _fake_db_ctx(_cfg):
        yield AsyncMock()

    with (
        patch.object(emb, "_ollama_embed", new=AsyncMock(return_value=fake_vec)),
        patch(
            "lazyclaw.lazybrain.embeddings.get_user_dek",
            new=AsyncMock(return_value=fake_dek),
        ),
        patch("lazyclaw.lazybrain.embeddings.db_session", new=_fake_db_ctx),
        patch.object(emb, "_cache_put", return_value=None),
        patch.object(emb, "_vec_upsert", new=AsyncMock(return_value=False)),
        # Patch the lazybrain _fts sub-module that upsert_embedding imports locally
        patch("lazyclaw.lazybrain.fts.delete_chunks", new=AsyncMock()),
        patch("lazyclaw.lazybrain.fts.upsert_chunk", new=AsyncMock()),
    ):
        result = await emb.upsert_embedding(cfg, user_id, note_id, text)

    # upsert_embedding should still succeed (vector was stored, only mirror failed)
    assert result is True, "upsert_embedding should return True even if vec0 mirror fails"

    # The key must have been removed so the warm guard re-runs next time
    assert key not in emb._VEC_WARMED, (
        "key must be discarded from _VEC_WARMED after _vec_upsert failure"
    )

    # Clean up any lingering state
    emb._VEC_WARMED.discard(key)


async def test_vec_upsert_success_does_not_remove_key_from_vec_warmed(tmp_path: Path):
    """If _vec_upsert succeeds, upsert_embedding must NOT discard the warm key."""
    emb = _get_module()

    from lazyclaw.config import Config
    cfg = Config(database_dir=tmp_path)

    user_id = "u-b1-ok"
    note_id = "n-b1-ok"
    text = "another note"

    from lazyclaw.db.connection import get_db_path
    db_path = str(get_db_path(cfg))
    key = (db_path, user_id)

    # Ensure the key is present before the call
    emb._VEC_WARMED.add(key)

    fake_vec = [0.2] * 10
    fake_dek = b"A" * 32  # exactly 256-bit AES key

    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_db_ctx(_cfg):
        yield AsyncMock()

    with (
        patch.object(emb, "_ollama_embed", new=AsyncMock(return_value=fake_vec)),
        patch(
            "lazyclaw.lazybrain.embeddings.get_user_dek",
            new=AsyncMock(return_value=fake_dek),
        ),
        patch("lazyclaw.lazybrain.embeddings.db_session", new=_fake_db_ctx),
        patch.object(emb, "_cache_put", return_value=None),
        patch.object(emb, "_vec_upsert", new=AsyncMock(return_value=True)),
        patch("lazyclaw.lazybrain.fts.delete_chunks", new=AsyncMock()),
        patch("lazyclaw.lazybrain.fts.upsert_chunk", new=AsyncMock()),
    ):
        result = await emb.upsert_embedding(cfg, user_id, note_id, text)

    assert result is True

    # Key must still be in _VEC_WARMED — no spurious invalidation on success
    assert key in emb._VEC_WARMED, (
        "key must remain in _VEC_WARMED after a successful _vec_upsert"
    )

    # Clean up
    emb._VEC_WARMED.discard(key)


async def test_vec_upsert_returns_bool_false_on_exception(tmp_path: Path):
    """_vec_upsert itself returns False when an exception is raised internally."""
    emb = _get_module()
    from lazyclaw.config import Config

    cfg = Config(database_dir=tmp_path)
    user_id = "u-exc"
    note_id = "n-exc"
    vec = [0.0] * 10

    # _vec_upsert tries to open db_session; make it raise inside the context body
    @contextlib.asynccontextmanager
    async def _raising_db_ctx(_cfg):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch("lazyclaw.lazybrain.embeddings.db_session", new=_raising_db_ctx):
        result = await emb._vec_upsert(cfg, user_id, note_id, vec)

    assert result is False, "_vec_upsert must return False (not raise) when db raises"


async def test_vec_upsert_returns_bool_true_on_success(tmp_path: Path):
    """_vec_upsert returns True when the write succeeds (or vec0 not available)."""
    emb = _get_module()
    from lazyclaw.config import Config

    cfg = Config(database_dir=tmp_path)
    user_id = "u-ok"
    note_id = "n-ok"
    vec = [0.0] * 10

    # Mock the DB so _vec_available returns False → early return (True path)
    @contextlib.asynccontextmanager
    async def _fake_db_ctx(_cfg):
        yield AsyncMock()

    with (
        patch("lazyclaw.lazybrain.embeddings.db_session", new=_fake_db_ctx),
        patch.object(emb, "_vec_available", new=AsyncMock(return_value=False)),
    ):
        result = await emb._vec_upsert(cfg, user_id, note_id, vec)

    assert result is True, "_vec_upsert must return True when vec0 is unavailable (early-exit)"
