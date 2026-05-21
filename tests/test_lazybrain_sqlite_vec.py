"""sqlite-vec backend for LazyBrain semantic search.

Locks the 2026-05-21 migration off the brute-force NumPy cosine pass
onto vec0 (sqlite-vec) virtual table. Covers:

  - Extension load (skipped cleanly if the host lacks sqlite-vec).
  - vec_note_embeddings mirror INSERT on upsert_embedding.
  - DELETE on delete_embedding removes the mirror row.
  - semantic_search ordering matches the brute-force fallback on a
    small fixture (must not regress recall).
  - Fallback path works when LAZYBRAIN_FORCE_DISABLE_SQLITE_VEC=1 —
    semantic_search still returns results via the NumPy cosine loop.

Isolation
---------
We bypass Ollama entirely by monkeypatching ``_ollama_embed`` to return
deterministic float vectors derived from the input text. Tests would
otherwise need a live Ollama daemon + the ``nomic-embed-text`` model
pulled, which isn't realistic on CI / dev hosts.

Module state is reset between tests (the per-process load flag, schema-
init set, and warmed-user set) so each test gets a clean view of the
extension state — required for the fallback-forcing test.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import embeddings as _emb
from lazyclaw.lazybrain import store


# ── sqlite-vec availability detection ──────────────────────────────


def _sqlite_vec_available() -> bool:
    """True iff the host can both import sqlite_vec and load it.

    Skipping early keeps CI green on locked-down hosts (system sqlite
    built without loadable extensions, sandboxed test runners, etc.).
    """
    try:
        import sqlite3
        import sqlite_vec  # noqa: WPS433
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.close()
        return True
    except Exception:
        return False


SQLITE_VEC_AVAILABLE = _sqlite_vec_available()

requires_sqlite_vec = pytest.mark.skipif(
    not SQLITE_VEC_AVAILABLE,
    reason=(
        "sqlite-vec extension not loadable on this host — install via "
        "`pip install sqlite-vec` and ensure the system sqlite build "
        "permits loadable extensions (most prebuilt python wheels do)"
    ),
)


# ── Fixture: fresh DB + deterministic fake embedder ─────────────────


def _fake_embed_vector(text: str, dim: int = _emb.EMBED_DIM) -> list[float]:
    """Cheap deterministic 'embedding' from text.

    Hashes per-token characters into a small handful of basis positions.
    Same input ALWAYS produces the same vector — so we can assert that
    vec0 and the fallback path agree on ordering.
    """
    vec = [0.0] * dim
    # Spread non-zero mass across a few positions per character so
    # different words produce distinguishable vectors but the unit norm
    # stays sane.
    for i, ch in enumerate(text.lower()):
        pos = (ord(ch) * (i + 1)) % dim
        vec[pos] += 1.0 + (i * 0.01)
    # L2-normalise so cosine is well-defined (matches what
    # nomic-embed-text returns shape-wise).
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _reset_vec_module_state() -> None:
    """Wipe per-process caches in lazyclaw.lazybrain.embeddings.

    The vec0 backend caches "extension loaded?" and "schema ready?" at
    module scope, which would otherwise carry stale state between
    tests that toggle the disable env var. The vector LRU is reset
    too so the test sees deterministic decrypt vs cache hits.
    """
    _emb._VEC_READY = None
    _emb._VEC_SCHEMA_INITIALIZED.clear()
    _emb._VEC_WARMED.clear()
    _emb._VECTOR_CACHE.clear()


@pytest.fixture
async def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh encrypted DB + a registered user + a fake Ollama embedder.

    Reset module state before AND after so tests don't see leakage from
    the previous one (e.g. _VEC_READY=False stuck from the fallback
    test before this one's vec0 check).
    """
    _reset_vec_module_state()
    # Make sure the env-var disable flag isn't set from a previous test.
    monkeypatch.delenv(_emb._VEC_DISABLE_ENV, raising=False)

    async def _fake_ollama_embed(text: str) -> list[float] | None:
        if not text or not text.strip():
            return None
        return _fake_embed_vector(text)

    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings._ollama_embed",
        _fake_ollama_embed,
    )

    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-vec", "alice", "x", "salt-vec-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()
        _reset_vec_module_state()


# ── Tests ──────────────────────────────────────────────────────────


def test_sqlite_vec_importable_and_loads() -> None:
    """Pre-flight: sqlite-vec is installed and loads on this host.

    Failure here means the test environment is missing the wheel —
    informative skip, not a misleading red test.
    """
    if not SQLITE_VEC_AVAILABLE:
        pytest.skip(
            "sqlite-vec extension not loadable — see "
            "_sqlite_vec_available() for diagnostic"
        )
    import sqlite_vec
    assert sqlite_vec.__version__, "sqlite_vec should expose __version__"


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_upsert_writes_vec_mirror_row(tmp_config: Config) -> None:
    """upsert_embedding mirrors the vector into vec_note_embeddings,
    queryable by user_id + model + dim."""
    note = await store.save_note(
        tmp_config, "user-vec",
        content="The Eiffel Tower is in Paris.", title="Paris",
    )
    ok = await _emb.upsert_embedding(
        tmp_config, "user-vec", note["id"],
        f"{note['title']}\n\n{note.get('content') or ''}",
    )
    assert ok is True, "upsert must succeed with the fake embedder"

    async with db_session(tmp_config) as db:
        r = await db.execute(
            "SELECT COUNT(*) FROM vec_note_embeddings "
            "WHERE note_id = ? AND user_id = ? AND model = ? AND dim = ?",
            (note["id"], "user-vec", _emb.EMBED_MODEL, _emb.EMBED_DIM),
        )
        row = await r.fetchone()
    assert row[0] == 1, "vec0 mirror must contain the upserted row"


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_delete_removes_vec_mirror_row(tmp_config: Config) -> None:
    """delete_embedding tears down the vec0 mirror row too — otherwise
    a deleted note keeps polluting nearest-neighbour results."""
    note = await store.save_note(
        tmp_config, "user-vec",
        content="Madrid is the capital of Spain.", title="Madrid",
    )
    await _emb.upsert_embedding(
        tmp_config, "user-vec", note["id"],
        f"{note['title']}\n\n{note.get('content') or ''}",
    )

    # Pre-condition: row exists.
    async with db_session(tmp_config) as db:
        r = await db.execute(
            "SELECT COUNT(*) FROM vec_note_embeddings WHERE note_id = ?",
            (note["id"],),
        )
        assert (await r.fetchone())[0] == 1

    await _emb.delete_embedding(tmp_config, note["id"])

    async with db_session(tmp_config) as db:
        r = await db.execute(
            "SELECT COUNT(*) FROM vec_note_embeddings WHERE note_id = ?",
            (note["id"],),
        )
        assert (await r.fetchone())[0] == 0, (
            "delete_embedding must also remove the vec0 mirror row"
        )


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_semantic_search_matches_fallback_ordering(
    tmp_config: Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """semantic_search must produce the same top-K ordering whether
    served by vec0 or the legacy NumPy loop, on a small fixture.

    Strategy: build 5 notes, seed both paths, query once with vec0
    enabled and once with it disabled, then assert the returned id
    list matches (ignoring score precision differences).
    """
    titles_and_content = [
        ("Paris", "The Eiffel Tower is in Paris, France."),
        ("Madrid", "Madrid is the capital of Spain, with great tapas."),
        ("Berlin", "Berlin is the capital of Germany."),
        ("Tokyo", "Tokyo is the capital of Japan."),
        ("Rome", "Rome is the capital of Italy, known for pasta."),
    ]
    seeded_ids: list[str] = []
    for title, content in titles_and_content:
        n = await store.save_note(
            tmp_config, "user-vec", content=content, title=title,
        )
        ok = await _emb.upsert_embedding(
            tmp_config, "user-vec", n["id"], f"{title}\n\n{content}",
        )
        assert ok is True
        seeded_ids.append(n["id"])

    # Hybrid path adds BM25 fusion which makes the ordering depend on
    # FTS state too — keep this test purely dense-vs-dense.
    query = "capital city of Spain with tapas"

    # First pass: vec0 enabled (default — no env var set).
    # min_similarity=0.0 keeps the synthetic fake-embedder scores (~0.05–0.20)
    # in play; the real nomic-embed-text floor of 0.32 would discard
    # this fixture entirely.
    _reset_vec_module_state()
    monkeypatch.delenv(_emb._VEC_DISABLE_ENV, raising=False)
    vec_result = await _emb.semantic_search(
        tmp_config, "user-vec", query, k=5,
        hybrid=False, diversify=False, min_similarity=0.0,
    )
    vec_ids = [r["id"] for r in vec_result["results"]]
    assert vec_ids, "vec0 path returned zero results"

    # Second pass: force the fallback NumPy path.
    _reset_vec_module_state()
    monkeypatch.setenv(_emb._VEC_DISABLE_ENV, "1")
    fb_result = await _emb.semantic_search(
        tmp_config, "user-vec", query, k=5,
        hybrid=False, diversify=False, min_similarity=0.0,
    )
    fb_ids = [r["id"] for r in fb_result["results"]]
    assert fb_ids, "fallback path returned zero results"

    # The two paths agree on the top-K set AND ordering. Pure cosine
    # over the same vectors must be deterministic; if they diverge,
    # the vec0 distance conversion (1 - distance) is wrong.
    assert vec_ids == fb_ids, (
        f"vec0 and fallback ordering diverged: vec0={vec_ids} "
        f"fallback={fb_ids}"
    )


@pytest.mark.asyncio
async def test_fallback_path_when_sqlite_vec_disabled(
    tmp_config: Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With sqlite-vec forced off via env var, semantic_search must
    still return results via the brute-force NumPy cosine path.

    This is the graceful-degradation guarantee: a locked-down sqlite
    build, a corrupted extension wheel, or any other vec0 failure must
    NOT take semantic search offline — it just gets slower.
    """
    titles_and_content = [
        ("Paris", "The Eiffel Tower is in Paris, France."),
        ("Madrid", "Madrid is the capital of Spain, with great tapas."),
        ("Berlin", "Berlin is the capital of Germany."),
    ]
    for title, content in titles_and_content:
        n = await store.save_note(
            tmp_config, "user-vec", content=content, title=title,
        )
        ok = await _emb.upsert_embedding(
            tmp_config, "user-vec", n["id"], f"{title}\n\n{content}",
        )
        assert ok is True

    # Force the brute-force fallback path BEFORE running semantic_search.
    _reset_vec_module_state()
    monkeypatch.setenv(_emb._VEC_DISABLE_ENV, "1")

    result = await _emb.semantic_search(
        tmp_config, "user-vec", "capital of Spain", k=3,
        hybrid=False, diversify=False, min_similarity=0.0,
    )

    assert result["results"], (
        "fallback path must return results even with sqlite-vec disabled"
    )
    # Source is the dense-only label since BM25 hybrid is off.
    assert result["source"] == "semantic"
    # And the disable flag must actually have engaged the fallback —
    # _VEC_READY should be False after the first call.
    assert _emb._VEC_READY is False, (
        "env-var disable must short-circuit the load attempt"
    )


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_warm_mirror_backfills_from_encrypted_source(
    tmp_config: Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_vec_mirror_warmed must back-fill missing vec0 rows from
    the encrypted source-of-truth on first use per process.

    Scenario: simulate a process restart where the encrypted
    ``note_embeddings`` rows exist but the in-memory vec0 mirror was
    wiped (e.g. ``CREATE VIRTUAL TABLE`` ran against a brand-new DB
    file). The warm helper should decrypt + re-insert the missing
    vectors before the next semantic_search returns zero hits.
    """
    titles_and_content = [
        ("Paris", "Eiffel Tower city."),
        ("Madrid", "Spanish capital."),
        ("Berlin", "German capital."),
    ]
    note_ids: list[str] = []
    for title, content in titles_and_content:
        n = await store.save_note(
            tmp_config, "user-vec", content=content, title=title,
        )
        await _emb.upsert_embedding(
            tmp_config, "user-vec", n["id"], f"{title}\n\n{content}",
        )
        note_ids.append(n["id"])

    # Simulate a "vec0 mirror went away" scenario: wipe the mirror table
    # but keep the encrypted source rows intact. Also clear the per-
    # process warm marker so _ensure_vec_mirror_warmed actually runs.
    async with db_session(tmp_config) as db:
        await db.execute("DELETE FROM vec_note_embeddings")
        await db.commit()
    _emb._VEC_WARMED.clear()

    # Pre-condition: mirror is empty, source is full.
    async with db_session(tmp_config) as db:
        r = await db.execute("SELECT COUNT(*) FROM vec_note_embeddings")
        assert (await r.fetchone())[0] == 0
        r = await db.execute(
            "SELECT COUNT(*) FROM note_embeddings WHERE user_id = ?",
            ("user-vec",),
        )
        assert (await r.fetchone())[0] == 3

    await _emb._ensure_vec_mirror_warmed(tmp_config, "user-vec")

    async with db_session(tmp_config) as db:
        r = await db.execute(
            "SELECT COUNT(*) FROM vec_note_embeddings WHERE user_id = ?",
            ("user-vec",),
        )
        count = (await r.fetchone())[0]
    assert count == 3, (
        f"warm helper must back-fill all 3 missing mirror rows, got {count}"
    )
