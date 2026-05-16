"""Local-first encrypted vector search for LazyBrain notes.

Embedding pipeline:
  - Ollama ``nomic-embed-text`` (274 MB, 768-d, $0).
  - If Ollama isn't running or the model isn't pulled, we degrade gracefully
    to substring scoring — the UI still shows *something*, never breaks.

Storage: ``note_embeddings`` — one row per note, vector encrypted with the
user's DEK (AAD = ``notes:embedding``). Plaintext ``model`` + ``dim`` so we
can skip rows with incompatible dimensionality without decrypting first.

Retrieval: for <10k notes, a full in-memory cosine pass is fine. No FAISS
needed. When the vault grows past 10k we can swap in sqlite-vec.
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
# Ollama embed call (async, short timeout)
# ---------------------------------------------------------------------------

import asyncio as _asyncio
import random as _random

# Bound concurrent embed requests. Ollama serialises GPU work but accepts
# all in-flight HTTP calls — without throttling the overflow turns into
# bursts of HTTP 500. 3 in-flight calls is the empirical sweet spot for a
# single-GPU desktop: keeps the GPU saturated without queue overflow.
_OLLAMA_SEM = _asyncio.Semaphore(3)


async def _ollama_embed(text: str) -> list[float] | None:
    """Ollama /api/embeddings. Returns None if unreachable / model missing.

    Two layers of protection against transient 5xx under load:
      1. Module-level semaphore caps concurrent calls at 3 — Ollama's
         single-GPU queue can't keep up with N parallel chunk embeds.
      2. One retry with jittered backoff catches the rare 5xx that still
         leaks through (model warm-up, brief paging).
    """
    if not text or not text.strip():
        return None
    payload = {"model": EMBED_MODEL, "prompt": text[:8000]}
    last_status: int | None = None
    try:
        async with _OLLAMA_SEM:
            async with httpx.AsyncClient(base_url=OLLAMA_BASE, timeout=30) as client:
                for attempt in range(2):
                    resp = await client.post("/api/embeddings", json=payload)
                    if resp.status_code == 404:
                        logger.info(
                            "Embedding model %s not installed — run `ollama pull %s`",
                            EMBED_MODEL, EMBED_MODEL,
                        )
                        return None
                    if 500 <= resp.status_code < 600 and attempt == 0:
                        last_status = resp.status_code
                        await _asyncio.sleep(0.2 + _random.random() * 0.3)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    vec = data.get("embedding")
                    if not isinstance(vec, list) or len(vec) != EMBED_DIM:
                        return None
                    return [float(x) for x in vec]
                logger.debug("Ollama embed failed after retry: HTTP %s", last_status)
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
    vectors = (
        await _load_all(config, user_id, tag_substring=tag_prefix)
        if q_vec else []
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


async def reindex_dirty_batch(
    config: Config,
    user_id: str,
    *,
    limit: int = 50,
) -> dict:
    """Re-embed up to ``limit`` notes whose ``embedding_dirty`` flag is 1.

    Called by the heartbeat daemon every tick. Most ticks find zero dirty
    notes (cheap COUNT-style query). When a dirty note's embedding upsert
    succeeds, ``upsert_embedding`` clears the flag — so a stuck dirty row
    is one whose content can't be embedded (Ollama down, model missing).
    Stops early if the first three attempts in a row fail to avoid
    hammering a dead Ollama. After a fully-failed pass we engage a 5-min
    process-level cooldown so persistently-broken rows (e.g. notes whose
    decrypted plaintext fails Ollama's tokeniser) don't burn 9 HTTP 500s
    every tick.
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
        rows = await db.execute(
            "SELECT id FROM notes "
            "WHERE user_id = ? AND (embedding_dirty = 1 OR chunks_dirty = 1) "
            "ORDER BY updated_at DESC LIMIT ?",
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
            continue
        text = f"{note.get('title') or ''}\n\n{note.get('content') or ''}".strip()
        ok = await upsert_embedding(config, user_id, note_id, text)
        if ok:
            indexed += 1
            consecutive_skip = 0
        else:
            skipped += 1
            consecutive_skip += 1
            # Three failures in a row → bail unconditionally. The previous
            # ``and indexed == 0`` guard meant a single early success kept
            # the loop grinding through hundreds of persistently-broken
            # rows every heartbeat tick (9 dirty notes × 60 ticks/h = the
            # 540/h Ollama-500 noise floor we saw post-throttle).
            if consecutive_skip >= 3:
                break

    # Engage cooldown only when the pass was non-trivially attempted but
    # produced zero successes. A pass with at least one success means
    # Ollama is alive and the failures are content-specific — no point
    # silencing the next 5 minutes of healthy traffic.
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
