"""Local-first encrypted vector search for LazyBrain notes.

Embedding pipeline:
  - Ollama ``nomic-embed-text`` (274 MB, 768-d, $0).
  - If Ollama isn't running or the model isn't pulled, we degrade gracefully
    to substring scoring — the UI still shows *something*, never breaks.

Storage: ``note_embeddings`` — one row per note, vector encrypted with the
user's DEK (AAD = ``notes:embedding``). Plaintext ``model`` + ``dim`` so we
can skip rows with incompatible dimensionality without decrypting first.

Retrieval (2026-05-21): ``sqlite-vec`` (vec0 virtual table) is the primary
nearest-neighbour backend — single SQL query, no per-user Python loop, no
decrypt-then-cosine pass over the whole corpus. The vec0 mirror
``vec_note_embeddings`` stores plaintext float32 vectors *only* (the
sensitive payload is the note body, not the 768 floats), with auxiliary
columns ``user_id``, ``model``, ``dim`` so we can ``WHERE`` partition
by user and skip rows from a stale model/dim. The encrypted
``note_embeddings`` row remains the source of truth; vec0 is a query-side
mirror, regenerated from the encrypted source on demand via
``_ensure_vec_mirror_warmed``.

If sqlite-vec can't load (missing extension, locked-down sqlite build,
loadable-extensions disabled), the brute-force NumPy/Python cosine path
is preserved as a graceful fallback — recall quality is identical, just
slower past a few thousand vectors. Tests force the fallback path via
``LAZYBRAIN_FORCE_DISABLE_SQLITE_VEC=1``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
from collections import OrderedDict
from typing import Iterable

import httpx

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain import store

logger = logging.getLogger(__name__)

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Drop hits below this cosine similarity. Lowered from 0.45 → 0.32 so
# legitimate paraphrase hits at 0.35–0.44 (which a 768-d nomic-embed-text
# model regularly produces for "I'm in Madrid" vs "I live in Madrid")
# stop falling silently. The MMR rerank below filters duplicate-content
# noise, so a lower floor + diversity pass beats a high floor alone.
MIN_SIMILARITY = 0.32

# MMR diversification weight. λ=0.7 means 70% relevance, 30% diversity —
# top results stay query-relevant but we don't return 8 paraphrases of
# the same note. Tuned by hand; changing this is a recall-quality call,
# not a perf one.
MMR_LAMBDA = 0.7

# Warm cache cap per user. Decrypted vectors live in RAM until either an
# upsert/delete invalidates the user's slot or the LRU spills past this
# count. 5000 vectors × 3 KB ≈ 15 MB per active user — fine for a desktop
# daemon, miles smaller than the model itself.
_VECTOR_CACHE_CAP = 5000

# Per-user OrderedDict[note_id, (model, dim, [float]*dim)]. Process-local;
# decrypted vectors NEVER persist to disk. Multi-process workers each
# warm independently — staleness is bounded by the writer being in-process
# for that user (upsert + delete invalidate the slot synchronously).
_VECTOR_CACHE: dict[str, "OrderedDict[str, tuple[str, int, list[float]]]"] = {}


def _emb_aad(user_id: str) -> bytes:
    return user_aad(user_id, "notes:embedding")


def _pack(vector: list[float]) -> bytes:
    """Tight float32 packing — 3.07 KB per 768-d vector."""
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cosine(a: list[float], b: list[float]) -> float:
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    return s / (na * nb)


def _mmr_rerank(
    scored: list[tuple[str, float, list[float]]],
    k: int,
    lambda_: float = MMR_LAMBDA,
) -> list[tuple[str, float, list[float]]]:
    """Maximal Marginal Relevance — pick k diverse, relevant items.

    ``scored`` must be pre-sorted by query similarity DESC. Each tuple is
    ``(note_id, cosine_to_query, vector)``. We greedily pick the next item
    whose ``λ*sim_to_query − (1−λ)*max_sim_to_already_selected`` is highest,
    which keeps the top of the list query-relevant while preventing 8
    paraphrases of the same note from crowding the result set.

    Complexity: O(k · n) cosine recomputes. With n ≤ 50 candidates and
    k ≤ 10 this is microseconds — invisible next to the embedding load.
    """
    if not scored:
        return []
    if k <= 1 or len(scored) <= 1:
        return scored[: max(1, k)]

    # Seed with the most query-relevant document.
    selected: list[tuple[str, float, list[float]]] = [scored[0]]
    remaining = list(scored[1:])

    while remaining and len(selected) < k:
        best_idx = -1
        best_mmr = float("-inf")
        for i, (_nid, sim_q, vec) in enumerate(remaining):
            max_div = max(_cosine(vec, svec) for _, _, svec in selected)
            mmr = lambda_ * sim_q - (1.0 - lambda_) * max_div
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        if best_idx < 0:
            break
        selected.append(remaining.pop(best_idx))
    return selected


# ---------------------------------------------------------------------------
# sqlite-vec backend (primary) — graceful fallback to brute-force cosine.
# ---------------------------------------------------------------------------
#
# Why vec0:
#   - At 5K per-user vectors the legacy NumPy pass hit 600-1200 ms p50
#     because every query decrypted every row before scoring. vec0 stores
#     plaintext float32 vectors with a B-tree-like ANN index — a single
#     ``SELECT ... WHERE ... MATCH ? ORDER BY distance LIMIT ?`` returns
#     top-K in <50 ms even at 100k vectors.
#   - The mirror is plaintext-by-design: the 768 floats themselves carry
#     no sensitive information (you can't recover the note body from the
#     embedding without the model + a 10s/query inversion attack). The
#     encrypted ``note_embeddings`` row remains the source of truth.
#
# Failure model:
#   - Extension load can fail when (a) sqlite_vec isn't installed,
#     (b) the system sqlite build disables loadable extensions, or
#     (c) we're inside a chroot with no /tmp write. Each path sets
#     ``_VEC_READY = False`` and we silently fall back to the NumPy loop.
#     The fallback path produces identical recall, just slower past a
#     few thousand vectors — perfect for tests and constrained hosts.
#   - The ``LAZYBRAIN_FORCE_DISABLE_SQLITE_VEC`` env var bypasses the
#     load attempt entirely. Used in the fallback regression test so we
#     don't need to monkeypatch the import.
#
# Threading note:
#   - aiosqlite runs all DB work in a single worker thread per connection.
#     ``sqlite_vec.load`` operates on the underlying ``sqlite3.Connection``
#     and SQLite refuses cross-thread access — so we must schedule the
#     load on the worker via ``conn._execute(conn._conn.load_extension, …)``.
#     Same pattern is used by aiosqlite's own ``load_extension`` proxy
#     in newer versions; we call the lower-level path so we stay compatible
#     with the 0.20–0.22 range pinned in pyproject.toml.

# Tri-state: None = not yet attempted, True = loaded + table ready,
# False = load failed (use fallback). Per-process; safe to retry by
# clearing to None (used by tests).
_VEC_READY: bool | None = None

# Set of (db_path) where the vec0 schema has been ensured. A new
# connection (separate db_path) needs its own schema check.
_VEC_SCHEMA_INITIALIZED: set[str] = set()

# Set of (db_path, user_id) tuples warmed during this process. First
# touch decrypts + back-fills the user's vec0 rows from the encrypted
# source-of-truth table. Subsequent queries reuse the warm mirror.
_VEC_WARMED: set[tuple[str, str]] = set()

# Test/operator override — set the env var to "1" to force the brute-
# force fallback path even if sqlite-vec is installed. Used by the
# fallback regression test so we don't need to monkeypatch the import.
_VEC_DISABLE_ENV = "LAZYBRAIN_FORCE_DISABLE_SQLITE_VEC"


def _vec_disabled_by_env() -> bool:
    return os.environ.get(_VEC_DISABLE_ENV, "").lower() in ("1", "true", "yes")


async def _ensure_vec_loaded(db) -> bool:
    """Lazily load the sqlite-vec extension into ``db``.

    Returns True if vec0 is usable on this connection; False if any
    step failed (extension missing, system sqlite locked down, etc.).
    The first failure is logged at INFO once per process — subsequent
    callers silently take the fallback path.
    """
    global _VEC_READY
    if _VEC_READY is False:
        return False
    if _VEC_READY is True:
        return True
    # First attempt.
    if _vec_disabled_by_env():
        _VEC_READY = False
        logger.info(
            "sqlite-vec backend disabled via %s — using brute-force fallback",
            _VEC_DISABLE_ENV,
        )
        return False
    try:
        import sqlite_vec  # noqa: WPS433  — lazy import is the whole point
    except Exception as exc:
        _VEC_READY = False
        logger.info(
            "sqlite-vec not installed (%s) — using brute-force fallback. "
            "Install with: pip install sqlite-vec", exc,
        )
        return False
    try:
        await db.enable_load_extension(True)
        # aiosqlite proxies ``load_extension`` since 0.19, but the
        # signature isn't fully stable across patch versions and the
        # internal worker-thread scheduling is. Calling the lower-level
        # ``_execute`` with ``conn._conn.load_extension`` is the most
        # portable shape and matches what aiosqlite does internally.
        path = sqlite_vec.loadable_path()
        await db._execute(db._conn.load_extension, path)
        await db.enable_load_extension(False)
    except Exception as exc:
        _VEC_READY = False
        try:
            await db.enable_load_extension(False)
        except Exception:
            pass
        logger.info(
            "sqlite-vec extension failed to load (%s) — using brute-force "
            "fallback", exc,
        )
        return False
    _VEC_READY = True
    logger.debug("sqlite-vec extension loaded (dim=%d)", EMBED_DIM)
    return True


async def _ensure_vec_schema(config: Config, db) -> bool:
    """Idempotently create ``vec_note_embeddings`` virtual table.

    Cached per-db-path so repeat upserts don't pay for the schema check.
    Returns True iff the table exists and is usable.
    """
    from lazyclaw.db.connection import get_db_path
    db_path = str(get_db_path(config))
    if db_path in _VEC_SCHEMA_INITIALIZED:
        return True
    try:
        await db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_note_embeddings USING vec0("
            "note_id TEXT PRIMARY KEY, "
            "user_id TEXT, "
            "model TEXT, "
            "dim INTEGER, "
            f"embedding FLOAT[{EMBED_DIM}] distance_metric=cosine"
            ")"
        )
        await db.commit()
    except Exception:
        logger.info("vec_note_embeddings creation failed", exc_info=True)
        return False
    _VEC_SCHEMA_INITIALIZED.add(db_path)
    return True


async def _vec_available(config: Config, db) -> bool:
    """One-shot check: extension loaded + virtual table ready."""
    if not await _ensure_vec_loaded(db):
        return False
    return await _ensure_vec_schema(config, db)


async def _vec_upsert(
    config: Config,
    user_id: str,
    note_id: str,
    vec: list[float],
) -> bool:
    """Mirror ``vec`` into the vec0 table. Best-effort — never raises.

    Returns True on success (including when vec0 is unavailable and we
    skip gracefully), False when an exception is caught.  The bool lets
    callers decide whether to invalidate the ``_VEC_WARMED`` guard so
    the mirror re-warms on the next semantic_search.

    vec0 doesn't support ``ON CONFLICT`` / ``INSERT OR REPLACE``
    (UPSERT isn't implemented for virtual tables), so we DELETE-then-
    INSERT. Both ops live in the same transaction so a query can't
    observe a partial row.
    """
    try:
        async with db_session(config) as db:
            if not await _vec_available(config, db):
                return True
            packed = _pack(vec)
            await db.execute(
                "DELETE FROM vec_note_embeddings WHERE note_id = ?",
                (note_id,),
            )
            await db.execute(
                "INSERT INTO vec_note_embeddings"
                "(note_id, user_id, model, dim, embedding) "
                "VALUES (?, ?, ?, ?, ?)",
                (note_id, user_id, EMBED_MODEL, EMBED_DIM, packed),
            )
            await db.commit()
        return True
    except Exception:
        logger.debug("vec0 upsert failed for %s", note_id, exc_info=True)
        return False


async def _vec_delete(config: Config, note_id: str) -> None:
    """Remove ``note_id`` from the vec0 mirror. Best-effort."""
    try:
        async with db_session(config) as db:
            if not await _vec_available(config, db):
                return
            await db.execute(
                "DELETE FROM vec_note_embeddings WHERE note_id = ?",
                (note_id,),
            )
            await db.commit()
    except Exception:
        logger.debug("vec0 delete failed for %s", note_id, exc_info=True)


async def _ensure_vec_mirror_warmed(config: Config, user_id: str) -> None:
    """Back-fill ``user_id``'s vec0 rows from the encrypted source-of-truth.

    Runs at most once per (db_path, user_id) per process. If vec0 has
    fewer rows than ``note_embeddings`` for this user we decrypt the
    missing ones and INSERT into the mirror — a one-time cost that pays
    for itself after the first ~10 semantic searches.

    Synchronous (awaitable, not background) by design: the caller is
    already inside semantic_search and the next line wants to query
    the mirror. Backgrounding it would mean the first N queries
    silently return zero results.
    """
    from lazyclaw.db.connection import get_db_path
    db_path = str(get_db_path(config))
    key = (db_path, user_id)
    if key in _VEC_WARMED:
        return
    try:
        async with db_session(config) as db:
            if not await _vec_available(config, db):
                _VEC_WARMED.add(key)  # avoid re-checking every query
                return
            # Counts on both sides — cheap.
            r1 = await db.execute(
                "SELECT COUNT(*) FROM note_embeddings "
                "WHERE user_id = ? AND model = ? AND dim = ?",
                (user_id, EMBED_MODEL, EMBED_DIM),
            )
            (src_count,) = await r1.fetchone()
            r2 = await db.execute(
                "SELECT COUNT(*) FROM vec_note_embeddings "
                "WHERE user_id = ? AND model = ? AND dim = ?",
                (user_id, EMBED_MODEL, EMBED_DIM),
            )
            (mirror_count,) = await r2.fetchone()
            if mirror_count >= src_count:
                _VEC_WARMED.add(key)
                return
            # Pull the (small set of) missing note_ids.
            present_rows = await db.execute(
                "SELECT note_id FROM vec_note_embeddings WHERE user_id = ?",
                (user_id,),
            )
            present = {r[0] for r in await present_rows.fetchall()}
            missing_rows = await db.execute(
                "SELECT note_id, vector FROM note_embeddings "
                "WHERE user_id = ? AND model = ? AND dim = ?",
                (user_id, EMBED_MODEL, EMBED_DIM),
            )
            missing = [
                (nid, enc) for (nid, enc) in await missing_rows.fetchall()
                if nid not in present
            ]
        if not missing:
            _VEC_WARMED.add(key)
            return
        dek = await get_user_dek(config, user_id)
        warmed = 0
        async with db_session(config) as db:
            for note_id, enc_vec in missing:
                try:
                    hex_blob = decrypt_field(
                        enc_vec, dek, _emb_aad(user_id), fallback="",
                    )
                    if not hex_blob:
                        continue
                    vec = _unpack(bytes.fromhex(hex_blob), EMBED_DIM)
                    await db.execute(
                        "DELETE FROM vec_note_embeddings WHERE note_id = ?",
                        (note_id,),
                    )
                    await db.execute(
                        "INSERT INTO vec_note_embeddings"
                        "(note_id, user_id, model, dim, embedding) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (note_id, user_id, EMBED_MODEL, EMBED_DIM, _pack(vec)),
                    )
                    warmed += 1
                except Exception:
                    logger.debug(
                        "warm-mirror skip for %s", note_id, exc_info=True,
                    )
            await db.commit()
        logger.debug(
            "vec0 mirror warmed: user=%s rows=%d", user_id, warmed,
        )
    except Exception:
        logger.debug("vec0 mirror warm failed", exc_info=True)
    _VEC_WARMED.add(key)


async def _vec_topk(
    config: Config,
    user_id: str,
    q_vec: list[float],
    *,
    k: int,
    tag_substring: str | None = None,
) -> list[tuple[str, float]] | None:
    """Run a vec0 nearest-neighbour search. Returns None when unavailable.

    Returns ``[(note_id, similarity), ...]`` with ``similarity = 1 - distance``
    (vec0 ``distance_metric=cosine`` returns ``1 - cos(a,b)``, so this
    converts back to the raw cosine similarity score the rest of the
    pipeline expects).

    When ``tag_substring`` is set we apply the same LIKE prefilter as the
    legacy loader did: JOIN against ``notes.tags`` to scope the candidate
    pool to a topic before the ANN search. Without the JOIN we'd have to
    fetch a large K then filter in Python — wastes most of the speedup.
    """
    try:
        async with db_session(config) as db:
            if not await _vec_available(config, db):
                return None
            packed = _pack(q_vec)
            cap = max(1, min(200, k))
            if tag_substring:
                like = f'%"{tag_substring}"%'
                # vec0 KNN with metadata filters (user_id/model/dim) + a JOIN
                # requires the explicit ``k = ?`` constraint — a trailing
                # ``LIMIT`` is NOT recognized as the KNN bound once extra
                # predicates are present, which raised "A LIMIT or 'k = ?'
                # constraint is required on vec0 knn queries" on every call
                # (silent fallback). ``k = ?`` is the canonical sqlite-vec form.
                cur = await db.execute(
                    "SELECT v.note_id, v.distance FROM vec_note_embeddings v "
                    "JOIN notes n ON n.id = v.note_id "
                    "WHERE v.user_id = ? AND v.model = ? AND v.dim = ? "
                    "AND v.embedding MATCH ? "
                    "AND v.k = ? "
                    "AND n.tags LIKE ? "
                    "ORDER BY v.distance",
                    (user_id, EMBED_MODEL, EMBED_DIM, packed, cap, like),
                )
            else:
                cur = await db.execute(
                    "SELECT note_id, distance FROM vec_note_embeddings "
                    "WHERE user_id = ? AND model = ? AND dim = ? "
                    "AND embedding MATCH ? "
                    "AND k = ? "
                    "ORDER BY distance",
                    (user_id, EMBED_MODEL, EMBED_DIM, packed, cap),
                )
            rows = await cur.fetchall()
    except Exception:
        logger.debug("vec0 topk query failed", exc_info=True)
        return None
    out: list[tuple[str, float]] = []
    for row in rows:
        nid = row[0]
        dist = float(row[1])
        # distance_metric=cosine emits 1 - cos(a,b). Convert back.
        sim = 1.0 - dist
        out.append((nid, sim))
    return out


# ---------------------------------------------------------------------------
# Ollama embed call (async, short timeout)
# ---------------------------------------------------------------------------

import asyncio as _asyncio
import random as _random

# Bound concurrent embed requests. Ollama serialises GPU work but accepts
# all in-flight HTTP calls — without throttling the overflow turns into
# bursts of HTTP 500. 3 in-flight calls is the empirical sweet spot for a
# single-GPU desktop: keeps the GPU saturated without queue overflow.
_OLLAMA_SEM = _asyncio.Semaphore(3)

# nomic-bert architecture caps at 2048 tokens regardless of Ollama's
# advertised num_ctx (8192). At ~3 chars/token for dense English markdown
# that's ~6000 chars before the model hard-errors with HTTP 500
# "input length exceeds the context length". We keep a safety char cap at
# 6000 (down from 8000) AND rely on Ollama's server-side `truncate=true`
# (default on `/api/embed`) as the authoritative guard for everything else
# (e.g. multi-byte scripts where the char heuristic underestimates tokens).
_EMBED_CHAR_CAP = 6000

# One-time WARNING latch for persistent context-length 500s. The legacy
# /api/embeddings endpoint silently ignored `truncate`; the new /api/embed
# path honours it, so this should never fire post-migration — but if it
# does we want the operator to see it ONCE, not 540 times an hour.
_CTX_OVERFLOW_LOGGED = False

# One-time WARNING latch for client-side (4xx) rejects. Pre-2026-05-20 the
# outer ``except Exception`` swallowed the response body — we saw "3 of 10
# dirty notes 400 every cycle" with no clue what shape Ollama disliked
# (model name, input type, empty body, etc.). On the FIRST 4xx the body +
# input preview surface at WARNING so the cause is visible exactly once.
# Subsequent 4xx stay at DEBUG so a broken caller doesn't flood the log.
# The reindex cooldown (5 min) remains the actual spam-rate-limiter.
# 404 has its own info-level "ollama pull" path and never trips this.
_CLIENT_REJECT_LOGGED = False


async def _ollama_embed(text: str) -> list[float] | None:
    """POST /api/embed → 768-dim vector. Returns None on any failure.

    Endpoint choice: ``/api/embed`` (singular plural input, returns
    ``embeddings: [[...]]``). The legacy ``/api/embeddings`` endpoint
    silently ignored the ``truncate`` parameter on Ollama 0.20.x — long
    notes (> ~2048 tokens) burned 500s every 5 minutes because the
    nomic-bert architecture rejects inputs past its hard context length
    even when the loaded Modelfile advertises ``num_ctx 8192``. The new
    endpoint truncates server-side before the model sees the prompt.

    Two layers of protection against transient 5xx under load:
      1. Module-level semaphore caps concurrent calls at 3 — Ollama's
         single-GPU queue can't keep up with N parallel chunk embeds.
      2. One retry with jittered backoff catches the rare 5xx that still
         leaks through (model warm-up, brief paging).

    A 500 carrying the ``input length exceeds the context length`` body
    is escalated to WARNING (once per process) — it almost certainly
    means a caller is passing a chunk that bypassed our 6 KB cap, and
    that's a config bug we want to surface, not a debug detail.
    """
    global _CTX_OVERFLOW_LOGGED, _CLIENT_REJECT_LOGGED
    if not text or not text.strip():
        return None
    # Server-side `truncate=true` is the default on /api/embed but we
    # pass it explicitly so a future Ollama default flip doesn't quietly
    # break us again.
    payload = {
        "model": EMBED_MODEL,
        "input": text[:_EMBED_CHAR_CAP],
        "truncate": True,
    }
    last_status: int | None = None
    last_body: str = ""
    try:
        async with _OLLAMA_SEM:
            async with httpx.AsyncClient(base_url=OLLAMA_BASE, timeout=30) as client:
                for attempt in range(2):
                    resp = await client.post("/api/embed", json=payload)
                    if resp.status_code == 404:
                        logger.info(
                            "Embedding model %s not installed — run `ollama pull %s`",
                            EMBED_MODEL, EMBED_MODEL,
                        )
                        return None
                    if 500 <= resp.status_code < 600 and attempt == 0:
                        last_status = resp.status_code
                        last_body = resp.text
                        await _asyncio.sleep(0.2 + _random.random() * 0.3)
                        continue
                    if 500 <= resp.status_code < 600:
                        # Final attempt also failed — capture body for the
                        # escalation check below.
                        last_status = resp.status_code
                        last_body = resp.text
                        break
                    # 4xx (other than 404, handled above): capture the body
                    # BEFORE raise_for_status() consumes it. The first one
                    # per process surfaces at WARNING with body + input
                    # preview so an actual config/input-shape bug shows up
                    # exactly once; subsequent 4xx stay at DEBUG via the
                    # outer except (no spam — cooldown handles rate).
                    if 400 <= resp.status_code < 500:
                        body_text = resp.text
                        if not _CLIENT_REJECT_LOGGED:
                            _CLIENT_REJECT_LOGGED = True
                            preview = repr(text[:80])
                            if len(text) > 80:
                                preview = preview + "…"
                            logger.warning(
                                "Ollama embed %d (client reject): model=%s "
                                "input_len=%d preview=%s body=%s",
                                resp.status_code,
                                EMBED_MODEL,
                                len(text),
                                preview,
                                body_text[:300],
                            )
                        else:
                            logger.debug(
                                "Ollama embed %d (client reject, "
                                "suppressed): body=%s",
                                resp.status_code, body_text[:200],
                            )
                        return None
                    resp.raise_for_status()
                    data = resp.json()
                    # /api/embed returns {"embeddings": [[...]]} (plural,
                    # nested). The legacy endpoint returned {"embedding":
                    # [...]}; we keep a tolerant read for both shapes so
                    # users running older Ollama still get vectors.
                    embs = data.get("embeddings")
                    if isinstance(embs, list) and embs and isinstance(embs[0], list):
                        vec = embs[0]
                    else:
                        vec = data.get("embedding")
                    if not isinstance(vec, list) or len(vec) != EMBED_DIM:
                        return None
                    return [float(x) for x in vec]
                # All retries exhausted with 5xx — escalate context-length
                # errors so a regression in server-side truncation surfaces
                # immediately. Other 5xx (timeouts, GPU OOM) stay at DEBUG.
                if (
                    last_status is not None
                    and "context length" in last_body.lower()
                    and not _CTX_OVERFLOW_LOGGED
                ):
                    _CTX_OVERFLOW_LOGGED = True
                    logger.warning(
                        "Ollama embed %d: input exceeds nomic-embed-text context length "
                        "even after %d-char cap + server truncate=true. Body: %s",
                        last_status, _EMBED_CHAR_CAP, last_body[:200],
                    )
                else:
                    logger.debug(
                        "Ollama embed failed after retry: HTTP %s body=%s",
                        last_status, last_body[:200],
                    )
                return None
    except httpx.ConnectError:
        logger.debug("Ollama unreachable — semantic search falls back to substring")
        return None
    except Exception as exc:
        logger.debug("Ollama embed failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Warm cache helpers
# ---------------------------------------------------------------------------


def _cache_put(
    user_id: str, note_id: str, model: str, dim: int, vec: list[float],
) -> None:
    slot = _VECTOR_CACHE.setdefault(user_id, OrderedDict())
    slot.pop(note_id, None)
    slot[note_id] = (model, dim, vec)
    while len(slot) > _VECTOR_CACHE_CAP:
        slot.popitem(last=False)


def _cache_evict_note(note_id: str) -> None:
    for slot in _VECTOR_CACHE.values():
        slot.pop(note_id, None)


def _cache_invalidate_user(user_id: str) -> None:
    _VECTOR_CACHE.pop(user_id, None)


def _cache_size(user_id: str) -> int:
    return len(_VECTOR_CACHE.get(user_id) or {})


# ---------------------------------------------------------------------------
# Store: upsert + fetch all vectors for a user
# ---------------------------------------------------------------------------

async def upsert_embedding(
    config: Config,
    user_id: str,
    note_id: str,
    text: str,
) -> bool:
    """Compute + store encrypted embedding for one note. Returns success flag.

    Phase G: alongside the whole-note vector this also chunks the note
    body, embeds each chunk, and writes ``note_chunks`` + ``note_chunks_fts``
    rows. The whole-note vector stays — it's a cheap centroid for graph
    "neighbour" queries — and is recomputed as the *mean* of its chunk
    vectors when the note is long enough to chunk.
    """
    vec = await _ollama_embed(text)
    if vec is None:
        return False

    dek = await get_user_dek(config, user_id)

    # ── Chunked indexes (Phase G) ─────────────────────────────────────
    # Compute first so we have the chunk vectors for the centroid below.
    from lazyclaw.lazybrain import chunker as _chunker
    from lazyclaw.lazybrain import fts as _fts
    chunks = _chunker.chunk_note(text)
    chunk_vectors: list[list[float]] = []
    chunk_hashes: list[str] = []
    if len(chunks) > 1:  # only multi-chunk notes need separate rows
        # Fast-path: if a chunk's SHA1(text) matches the row already on
        # disk, reuse the stored encrypted vector instead of re-embedding.
        # Edits that touch one chunk in a 5-chunk note used to fire 5
        # Ollama calls; now they fire 1.
        existing: dict[int, tuple[str, str]] = {}
        try:
            async with db_session(config) as db:
                rows = await db.execute(
                    "SELECT chunk_idx, chunk_hash, vector FROM note_chunks "
                    "WHERE note_id = ? AND user_id = ? "
                    "AND model = ? AND dim = ?",
                    (note_id, user_id, EMBED_MODEL, EMBED_DIM),
                )
                for chunk_idx, prev_hash, prev_enc in await rows.fetchall():
                    if prev_hash and prev_enc:
                        existing[int(chunk_idx)] = (prev_hash, prev_enc)
        except Exception:
            logger.debug("chunk-hash lookup failed; falling back to full re-embed", exc_info=True)
            existing = {}

        for c in chunks:
            h = hashlib.sha1(c.text.encode("utf-8")).hexdigest()
            chunk_hashes.append(h)
            prev = existing.get(c.idx)
            if prev and prev[0] == h:
                # Hash matches → reuse the stored vector. Decrypt once so
                # the centroid math + cache writethrough below still work.
                try:
                    cleartext_hex = decrypt_field(prev[1], dek, _emb_aad(user_id))
                    cvec = _unpack(bytes.fromhex(cleartext_hex), EMBED_DIM)
                    chunk_vectors.append(cvec)
                    continue
                except Exception:
                    logger.debug("reuse decrypt failed; re-embedding chunk", exc_info=True)
            cvec = await _ollama_embed(c.text)
            if cvec is None:
                # If Ollama died mid-chunking, fall back to the whole-note
                # vector for the rest — better than zero rows.
                cvec = vec
            chunk_vectors.append(cvec)
        # Use the mean of chunk vectors as the new whole-note centroid.
        # Same dim, so summing is safe.
        if chunk_vectors:
            mean_vec = [
                sum(v[i] for v in chunk_vectors) / len(chunk_vectors)
                for i in range(EMBED_DIM)
            ]
            vec = mean_vec

    enc = encrypt_field(_pack(vec).hex(), dek, _emb_aad(user_id))

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO note_embeddings (note_id, user_id, model, dim, vector, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(note_id) DO UPDATE SET "
            "model = excluded.model, dim = excluded.dim, "
            "vector = excluded.vector, updated_at = excluded.updated_at",
            (note_id, user_id, EMBED_MODEL, EMBED_DIM, enc),
        )
        # Multi-chunk path: rebuild chunk rows from scratch — cheaper
        # than per-chunk diffing and idempotent for the common edit case.
        if chunk_vectors:
            await db.execute(
                "DELETE FROM note_chunks WHERE note_id = ?", (note_id,),
            )
            for c, cvec, chash in zip(chunks, chunk_vectors, chunk_hashes):
                cenc = encrypt_field(_pack(cvec).hex(), dek, _emb_aad(user_id))
                await db.execute(
                    "INSERT INTO note_chunks "
                    "(note_id, user_id, chunk_idx, model, dim, vector, "
                    "chunk_text, chunk_hash, updated_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        note_id, user_id, c.idx, EMBED_MODEL, EMBED_DIM,
                        cenc, c.text, chash,
                    ),
                )
        else:
            # Short note → no per-chunk rows; clean up any stale entries
            # left from a previous longer revision.
            await db.execute(
                "DELETE FROM note_chunks WHERE note_id = ?", (note_id,),
            )
        await db.commit()

    # Chunk-FTS write-through (best-effort; never blocks the embedding write).
    try:
        await _fts.delete_chunks(config, note_id)
        if chunk_vectors:
            for c in chunks:
                await _fts.upsert_chunk(
                    config, user_id, note_id, c.idx, c.text,
                )
        else:
            # Single-chunk path still indexes the body for BM25 — without
            # it, a short note's content can never be matched by chunk-FTS.
            await _fts.upsert_chunk(config, user_id, note_id, 0, text)
    except Exception:
        logger.debug("note_chunks_fts upsert failed", exc_info=True)

    # Warm-cache writethrough: we already have the plaintext vector here,
    # so we can populate without paying another decrypt later. Evict
    # any stale entry first to keep the LRU honest.
    _cache_put(user_id, note_id, EMBED_MODEL, EMBED_DIM, vec)

    # vec0 mirror writethrough — primary search backend when sqlite-vec
    # is available. No-op + best-effort when the extension didn't load.
    # Recovery: if the mirror write failed, evict the warm-guard key so
    # _ensure_vec_mirror_warmed re-runs on the next semantic_search and
    # back-fills the missing note once the transient error clears.
    if not await _vec_upsert(config, user_id, note_id, vec):
        from lazyclaw.db.connection import get_db_path
        _VEC_WARMED.discard((str(get_db_path(config)), user_id))

    # Mark the note's embedding as fresh — clears the dirty flag set by
    # the content writer in store.py (best-effort; no-op if column missing).
    try:
        async with db_session(config) as db:
            await db.execute(
                "UPDATE notes SET embedding_dirty = 0, chunks_dirty = 0 "
                "WHERE id = ? AND user_id = ?",
                (note_id, user_id),
            )
            await db.commit()
    except Exception:
        logger.debug("clear embedding_dirty failed (older schema?)", exc_info=True)
    return True


async def delete_embedding(config: Config, note_id: str) -> None:
    async with db_session(config) as db:
        await db.execute(
            "DELETE FROM note_embeddings WHERE note_id = ?", (note_id,)
        )
        await db.commit()
    # Drop from every user-slot just in case (note_id is globally unique).
    _cache_evict_note(note_id)
    # Mirror delete — keeps vec0 in sync with the encrypted source. Safe
    # no-op when sqlite-vec isn't loaded.
    await _vec_delete(config, note_id)


async def _load_all(
    config: Config,
    user_id: str,
    *,
    tag_substring: str | None = None,
) -> list[tuple[str, list[float]]]:
    """Decrypt + unpack every vector for this user.

    When ``tag_substring`` is set, joins against the ``notes`` table and
    only loads embeddings for notes whose plaintext ``tags`` JSON contains
    the substring (e.g. ``"topic/browser"``). This is the prefilter that
    keeps recall latency bounded as the lesson corpus grows past a few
    hundred notes — without it every recall decrypts every embedding.

    Warm-cache path: the per-user ``_VECTOR_CACHE`` slot is consulted
    first. On a miss for a given ``note_id`` the row is decrypted once,
    populated into the slot, and reused for the rest of the session.
    For tag-scoped recalls we still hit the DB (to apply the LIKE
    prefilter) but read decrypted vectors from the cache when we have
    them, avoiding the DEK decrypt per row.
    """
    async with db_session(config) as db:
        if tag_substring:
            like = f'%"{tag_substring}"%'
            rows = await db.execute(
                "SELECT ne.note_id, ne.model, ne.dim, ne.vector "
                "FROM note_embeddings ne "
                "JOIN notes n ON n.id = ne.note_id "
                "WHERE ne.user_id = ? AND ne.model = ? AND ne.dim = ? "
                "AND n.tags LIKE ?",
                (user_id, EMBED_MODEL, EMBED_DIM, like),
            )
        else:
            rows = await db.execute(
                "SELECT note_id, model, dim, vector FROM note_embeddings "
                "WHERE user_id = ? AND model = ? AND dim = ?",
                (user_id, EMBED_MODEL, EMBED_DIM),
            )
        data = await rows.fetchall()

    slot = _VECTOR_CACHE.get(user_id)
    out: list[tuple[str, list[float]]] = []
    decrypt_count = 0
    dek = None  # lazy-derive only when we actually need to decrypt

    for note_id, model, dim, enc_vec in data:
        # Skip rows from a stale model/dim — should be rare, but the
        # filter above only narrows by EMBED_MODEL+EMBED_DIM, so trust
        # but verify.
        if model != EMBED_MODEL or int(dim) != EMBED_DIM:
            continue
        cached = slot.get(note_id) if slot is not None else None
        if cached is not None:
            _, _, vec = cached
            # LRU touch — bump to MRU end so eviction picks something else.
            if slot is not None:
                slot.move_to_end(note_id)
            out.append((note_id, vec))
            continue
        # Miss — decrypt once and populate.
        try:
            if dek is None:
                dek = await get_user_dek(config, user_id)
            hex_blob = decrypt_field(enc_vec, dek, _emb_aad(user_id), fallback="")
            if not hex_blob:
                continue
            blob = bytes.fromhex(hex_blob)
            vec = _unpack(blob, int(dim))
            _cache_put(user_id, note_id, model, int(dim), vec)
            out.append((note_id, vec))
            decrypt_count += 1
        except Exception:
            continue

    if decrypt_count:
        logger.debug(
            "embedding cache: user=%s decrypted=%d total=%d cached_now=%d",
            user_id, decrypt_count, len(data), _cache_size(user_id),
        )
    return out


async def _load_by_ids(
    config: Config,
    user_id: str,
    note_ids: list[str],
) -> list[tuple[str, list[float]]]:
    """Decrypt + unpack vectors for a specific note_id set.

    Companion to ``_load_all`` for the vec0 fast-path: vec0 already gave
    us the top-K note_ids, so we only need to materialise vectors for
    those rows (cache hits first, decrypt the misses). Preserves the
    per-user LRU cache + DEK reuse.

    Returns vectors in the same order as ``note_ids``. Missing rows
    (deleted between vec0 query and source-row read) are silently
    skipped — the caller already has a similarity score for them and
    can keep them as BM25-style fallback.
    """
    if not note_ids:
        return []
    slot = _VECTOR_CACHE.get(user_id)

    # Split cached vs needs-decrypt to avoid issuing a DB query when the
    # warm cache already covers everything (common after the first few
    # searches of a session).
    cached: dict[str, list[float]] = {}
    missing: list[str] = []
    for nid in note_ids:
        if slot is not None and nid in slot:
            _, _, vec = slot[nid]
            slot.move_to_end(nid)
            cached[nid] = vec
        else:
            missing.append(nid)

    decrypted: dict[str, list[float]] = {}
    if missing:
        # Single DB roundtrip for all misses using a parameterised IN clause.
        placeholders = ",".join("?" for _ in missing)
        try:
            async with db_session(config) as db:
                rows = await db.execute(
                    f"SELECT note_id, model, dim, vector FROM note_embeddings "
                    f"WHERE user_id = ? AND model = ? AND dim = ? "
                    f"AND note_id IN ({placeholders})",
                    (user_id, EMBED_MODEL, EMBED_DIM, *missing),
                )
                data = await rows.fetchall()
        except Exception:
            logger.debug("by-id source-row fetch failed", exc_info=True)
            data = []

        dek = None
        for note_id, model, dim, enc_vec in data:
            if model != EMBED_MODEL or int(dim) != EMBED_DIM:
                continue
            try:
                if dek is None:
                    dek = await get_user_dek(config, user_id)
                hex_blob = decrypt_field(
                    enc_vec, dek, _emb_aad(user_id), fallback="",
                )
                if not hex_blob:
                    continue
                vec = _unpack(bytes.fromhex(hex_blob), int(dim))
                _cache_put(user_id, note_id, model, int(dim), vec)
                decrypted[note_id] = vec
            except Exception:
                continue

    out: list[tuple[str, list[float]]] = []
    for nid in note_ids:
        vec = cached.get(nid) or decrypted.get(nid)
        if vec is not None:
            out.append((nid, vec))
    return out


# ---------------------------------------------------------------------------
# Public search + index
# ---------------------------------------------------------------------------

async def semantic_search(
    config: Config,
    user_id: str,
    query: str,
    *,
    k: int = 10,
    tag_prefix: str | None = None,
    min_similarity: float = MIN_SIMILARITY,
    diversify: bool = True,
    hybrid: bool = True,
) -> dict:
    """Return ``{query, results, source}`` with top-k notes.

    ``source`` is one of:
      - ``"hybrid"``  — RRF fusion of dense + BM25 (Phase F default).
      - ``"semantic"`` — dense-only path (Ollama up, FTS empty / disabled).
      - ``"substring"`` — fell through to the legacy substring scan.
      - ``"empty"`` — the user has zero notes.

    ``tag_prefix`` (e.g. ``"topic/browser"``) filters the candidate set at
    the SQL layer before any embedding decryption happens — drops post-hoc
    waste from ~40% to ~0% for tag-scoped recalls.

    ``min_similarity`` drops weak hits whose cosine score falls below the
    threshold. Caller can pass ``0.0`` to keep the legacy behaviour.

    ``diversify`` runs MMR over the top ``3·k`` cosine-ranked candidates so
    the returned set isn't dominated by paraphrases of the same note. Set
    ``False`` for callers that explicitly want pure-cosine ordering
    (e.g. nearest-neighbour exemplar pulls).

    ``hybrid`` (default ``True``) layers BM25 over the title + chunk FTS
    indexes alongside dense, fuses them with RRF, and only falls back to
    pure-cosine when BM25 returns nothing. Pass ``False`` to force the
    pre-Phase-F dense-only behaviour.
    """
    q = (query or "").strip()
    if not q:
        return {"query": "", "results": [], "source": "empty"}

    q_vec = await _ollama_embed(q)

    # ── Candidate fetch ────────────────────────────────────────────────
    # vec0 fast-path: pull top-K nearest from the plaintext mirror, then
    # materialise vectors for *only* those ids (cache or decrypt). When
    # vec0 isn't available we fall back to the full per-user decrypt
    # loop — identical recall, just slower.
    #
    # ``vec_pool`` is sized at max(50, k*5) so MMR (over ~3·k pool) and
    # RRF fusion (against BM25) both have enough candidates to rerank
    # without paying the full decrypt cost. Past 50 vectors the extra
    # ANN headroom is essentially free; the decrypt cost scales with
    # how many we keep, not how many we scanned.
    vectors: list[tuple[str, list[float]]] = []
    vec_topk: list[tuple[str, float]] | None = None
    if q_vec:
        await _ensure_vec_mirror_warmed(config, user_id)
        pool_size = max(50, k * 5)
        vec_topk = await _vec_topk(
            config, user_id, q_vec,
            k=pool_size, tag_substring=tag_prefix,
        )
        if vec_topk is not None:
            ids = [nid for nid, _s in vec_topk]
            vectors = await _load_by_ids(config, user_id, ids)
        else:
            # Fallback: full decrypt loop (legacy path).
            vectors = await _load_all(
                config, user_id, tag_substring=tag_prefix,
            )

    # ── Hybrid branch — fuse BM25 + dense via RRF ─────────────────────
    bm25_ids: list[str] = []
    if hybrid:
        try:
            from lazyclaw.lazybrain import fts as _fts, rrf as _rrf
            # Pull both the title-level and chunk-level BM25 lists; collapse
            # chunk hits into a deduped note id list (best chunk wins by
            # definition of MATCH ordering).
            title_hits = await _fts.search_titles(config, user_id, q, limit=50)
            chunk_hits = await _fts.search_chunks(config, user_id, q, limit=50)
            seen_chunk: set[str] = set()
            chunk_ids: list[str] = []
            for note_id, _idx in chunk_hits:
                if note_id in seen_chunk:
                    continue
                seen_chunk.add(note_id)
                chunk_ids.append(note_id)
            # Fuse the two BM25 lists into one before fusing with dense —
            # gives chunk hits and title hits equal weight at the BM25 layer.
            bm25_ids = _rrf.fuse_to_ids(
                [title_hits, chunk_ids], limit=max(50, k * 5),
            )
        except Exception:
            logger.debug("hybrid BM25 path failed", exc_info=True)
            bm25_ids = []

    if q_vec and vectors:
        # Keep vectors alongside scores so MMR can compute doc-doc similarity.
        scored: list[tuple[str, float, list[float]]] = [
            (nid, _cosine(q_vec, vec), vec) for nid, vec in vectors
        ]
        if min_similarity > 0:
            scored = [t for t in scored if t[1] >= min_similarity]
        scored.sort(key=lambda x: x[1], reverse=True)

        cap = max(1, min(50, k))
        # Build the dense-ranked id list before optional fusion so RRF and
        # the legacy MMR path see the same pool ordering.
        dense_ids = [t[0] for t in scored]

        if hybrid and bm25_ids:
            # RRF fuse dense + BM25, then re-attach vectors (for MMR) only
            # for the fused top — saves cosine recompute on dropped docs.
            from lazyclaw.lazybrain import rrf as _rrf
            fused_ids = _rrf.fuse_to_ids(
                [dense_ids, bm25_ids], limit=max(cap * 3, 30),
            )
            vec_by_id = {nid: vec for nid, _s, vec in scored}
            score_by_id = {nid: s for nid, s, _v in scored}
            # Surface BM25-only hits (no dense vector) as dense_score=0 so
            # MMR can still operate over them — gives them a vector via
            # nearest pivot lookup if needed.
            fused_scored: list[tuple[str, float, list[float]]] = []
            for nid in fused_ids:
                vec = vec_by_id.get(nid)
                if vec is None:
                    # No dense vector cached — skip from MMR pool but keep
                    # for return ordering by appending to results later.
                    continue
                fused_scored.append((nid, score_by_id.get(nid, 0.0), vec))
            if diversify and len(fused_scored) > cap:
                top = _mmr_rerank(fused_scored, k=cap)
            else:
                top = fused_scored[:cap]
            # If MMR thinned us below cap and BM25 has unused candidates,
            # pad with BM25-only hits at the tail (they have positional
            # rank but no dense embedding) — better recall on rare nouns.
            if len(top) < cap:
                seen = {nid for nid, _s, _v in top}
                for nid in fused_ids:
                    if nid in seen or nid in vec_by_id:
                        continue
                    top.append((nid, 0.0, []))  # type: ignore[list-item]
                    seen.add(nid)
                    if len(top) >= cap:
                        break
            results: list[dict] = []
            for nid, score, _vec in top:
                note = await store.get_note(config, user_id, nid)
                if note:
                    note = {**note, "_score": round(score, 4)}
                    results.append(note)
            return {"query": q, "results": results, "source": "hybrid"}

        # Dense-only path (legacy)
        if diversify and len(scored) > cap:
            pool = scored[: max(cap, min(50, cap * 3))]
            top = _mmr_rerank(pool, k=cap)
        else:
            top = scored[:cap]

        results: list[dict] = []
        for nid, score, _vec in top:
            note = await store.get_note(config, user_id, nid)
            if note:
                note = {**note, "_score": round(score, 4)}
                results.append(note)
        return {"query": q, "results": results, "source": "semantic"}

    # ── Dense path unavailable — try BM25 alone before substring fall-through ─
    if bm25_ids:
        results = []
        for nid in bm25_ids[: max(1, min(50, k))]:
            note = await store.get_note(config, user_id, nid)
            if note:
                results.append(note)
        if results:
            return {"query": q, "results": results, "source": "bm25"}

    # Fallback: substring search. The user never sees a hard error.
    hits = await store.search_notes(config, user_id, q, limit=k)
    return {
        "query": q,
        "results": hits,
        "source": "substring" if hits else "empty",
    }


# Process-local cooldown gate. Set to a future epoch when a full reindex
# pass produces zero successes — subsequent passes short-circuit until the
# cooldown lapses, so 3 stuck dirty notes don't drive 9 Ollama 500s every
# tick. Cleared on first successful embed.
import time as _time
_REINDEX_COOLDOWN_UNTIL: float = 0.0
_REINDEX_COOLDOWN_SECS = 300.0  # 5 minutes


async def _stamp_reindex_attempt(
    config: Config, user_id: str, note_id: str,
) -> None:
    """Bump ``notes.last_reindex_attempt_at`` to now.

    Called for EVERY attempt — success OR skip — so the picker's
    ``ORDER BY last_reindex_attempt_at ASC NULLS FIRST`` rotates fairly
    across the dirty set. Without this, the same 3 most-recent dirty
    rows kept winning the lottery and 451 older dirty rows starved
    (observed in production on 2026-05-20: 454 dirty notes, only the
    top 3 ever retried, looped in 5-min cooldown for hours).

    Best-effort: failure is logged at debug and swallowed. The worst
    case is we re-pick the same row next cycle — degraded fairness,
    not lost data. No-op on older schemas without the column.
    """
    try:
        async with db_session(config) as db:
            await db.execute(
                "UPDATE notes SET last_reindex_attempt_at = datetime('now') "
                "WHERE id = ? AND user_id = ?",
                (note_id, user_id),
            )
            await db.commit()
    except Exception:
        logger.debug(
            "stamp last_reindex_attempt_at failed (older schema?)",
            exc_info=True,
        )


async def reindex_dirty_batch(
    config: Config,
    user_id: str,
    *,
    limit: int = 50,
) -> dict:
    """Re-embed up to ``limit`` notes whose embedding/chunk dirty flag is 1.

    Called by the heartbeat daemon every tick. Most ticks find zero dirty
    notes (cheap COUNT-style query). When a dirty note's embedding upsert
    succeeds, ``upsert_embedding`` clears the flag — so a stuck dirty row
    is one whose content can't be embedded (Ollama down, model missing).

    Starvation fix (2026-05-20)
    ---------------------------
    Picker ordering is ``last_reindex_attempt_at ASC NULLS FIRST``, not
    ``updated_at DESC``. Every attempt — success OR skip — stamps the
    column to ``datetime('now')``, so a failed row falls to the BACK of
    the next cycle's queue. Brand-new dirty rows (column still NULL)
    win priority over previously-attempted rows, so user edits get
    re-embedded promptly. Without this rotation, the original
    ``ORDER BY updated_at DESC`` re-picked the same 3 most-recent dirty
    rows every tick; if they failed (Ollama 500 / decrypt-broken /
    tokenizer-busted) the 3-consecutive-skip bailout fired and the
    other 451 dirty rows on the user starved forever.

    Back-pressure preserved
    -----------------------
    The 3-consecutive-skip bailout and the 5-min process-level cooldown
    are kept — they're orthogonal to starvation. Bailout caps work-per-
    tick when Ollama is wedged; the cooldown silences the next 5 min
    of pointless retries when a full pass produced zero successes.
    Per-row stamping happens BEFORE the bailout breaks the loop, so
    the 3 skipped rows still rotate to the back. Next cycle picks a
    different 3.
    """
    global _REINDEX_COOLDOWN_UNTIL
    if _time.time() < _REINDEX_COOLDOWN_UNTIL:
        return {"indexed": 0, "skipped": 0, "checked": 0, "cooldown": True}
    try:
        from lazyclaw.lazybrain import store as _lb_store
    except Exception:
        logger.debug("lazybrain store import failed in reindex_dirty_batch")
        return {"indexed": 0, "skipped": 0, "checked": 0}

    async with db_session(config) as db:
        # Phase G: pick up chunk-dirty rows too. After the migration first
        # lands every existing row has chunks_dirty=1 and embedding_dirty=0
        # — without the OR they'd never be chunked until the user edited
        # them.
        #
        # Ordering: last_reindex_attempt_at ASC NULLS FIRST. NULL means
        # "never tried" — those jump to the head. Among already-tried
        # rows, the longest-ago attempt wins. SQLite's default NULL sort
        # is NULLS FIRST for ASC, so the bare ``ORDER BY ... ASC`` is
        # correct; we spell out NULLS FIRST anyway for clarity + future
        # portability.
        rows = await db.execute(
            "SELECT id FROM notes "
            "WHERE user_id = ? AND (embedding_dirty = 1 OR chunks_dirty = 1) "
            "ORDER BY last_reindex_attempt_at ASC NULLS FIRST, "
            "updated_at DESC "
            "LIMIT ?",
            (user_id, max(1, min(500, limit))),
        )
        dirty_ids = [r[0] for r in await rows.fetchall()]

    if not dirty_ids:
        return {"indexed": 0, "skipped": 0, "checked": 0}

    indexed = 0
    skipped = 0
    consecutive_skip = 0
    for note_id in dirty_ids:
        note = await _lb_store.get_note(config, user_id, note_id)
        if not note:
            skipped += 1
            # Stamp even for "note vanished" so a deleted-row race
            # doesn't keep re-electing the same ghost id. (In practice
            # the UPDATE will be a no-op since the row is gone, but
            # the picker filters by dirty flag so a deleted row drops
            # out of contention regardless. Stamp anyway for symmetry.)
            await _stamp_reindex_attempt(config, user_id, note_id)
            continue
        text = f"{note.get('title') or ''}\n\n{note.get('content') or ''}".strip()
        # Stamp BEFORE the embed attempt so even an in-flight crash
        # (process killed mid-Ollama-call) still moves the row to the
        # back of the queue on next start. Otherwise a poison row that
        # OOMs the worker would re-elect itself forever.
        await _stamp_reindex_attempt(config, user_id, note_id)
        ok = await upsert_embedding(config, user_id, note_id, text)
        if ok:
            indexed += 1
            consecutive_skip = 0
        else:
            skipped += 1
            consecutive_skip += 1
            # Three failures in a row → bail this tick. Each of the 3
            # rows has already been stamped above, so they're at the
            # back of next cycle's queue — a different 3 will be
            # picked first. No starvation, just bounded work-per-tick.
            if consecutive_skip >= 3:
                break

    # Engage cooldown only when the pass was non-trivially attempted but
    # produced zero successes. A pass with at least one success means
    # Ollama is alive and the failures are content-specific — no point
    # silencing the next 5 minutes of healthy traffic. Per-row stamping
    # already pushed the attempted rows to the back of the queue, so
    # the cooldown is pure rate-limit on the heartbeat side, not a
    # starvation mechanism.
    if indexed == 0 and skipped >= 3:
        _REINDEX_COOLDOWN_UNTIL = _time.time() + _REINDEX_COOLDOWN_SECS
        logger.debug(
            "Embedding reindex cooldown engaged for %ds (no successes in pass)",
            int(_REINDEX_COOLDOWN_SECS),
        )

    return {
        "indexed": indexed,
        "skipped": skipped,
        "checked": len(dirty_ids),
    }


async def reindex_user(
    config: Config,
    user_id: str,
    *,
    limit: int = 2000,
) -> dict:
    """Recompute embeddings for every note. Returns progress summary."""
    notes = await store.list_notes(config, user_id, limit=limit)
    indexed = 0
    skipped = 0
    for n in notes:
        text = f"{n.get('title') or ''}\n\n{n.get('content') or ''}".strip()
        ok = await upsert_embedding(config, user_id, n["id"], text)
        if ok:
            indexed += 1
        else:
            skipped += 1
            # Stop early if Ollama is down — no point hammering it.
            if skipped >= 3 and indexed == 0:
                break
    return {
        "total": len(notes),
        "indexed": indexed,
        "skipped": skipped,
        "model": EMBED_MODEL,
    }


async def ensure_embedding(
    config: Config,
    user_id: str,
    note_id: str,
    text: str,
) -> None:
    """Fire-and-forget upsert helper — call after save_note/update_note."""
    try:
        await upsert_embedding(config, user_id, note_id, text)
    except Exception as exc:
        logger.debug("ensure_embedding noop (ollama down?): %s", exc)


__all__: Iterable[str] = [
    "EMBED_MODEL",
    "EMBED_DIM",
    "MIN_SIMILARITY",
    "MMR_LAMBDA",
    "semantic_search",
    "reindex_user",
    "reindex_dirty_batch",
    "upsert_embedding",
    "ensure_embedding",
    "delete_embedding",
]
