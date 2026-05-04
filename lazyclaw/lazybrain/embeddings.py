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

import json
import logging
import math
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
OLLAMA_BASE = "http://localhost:11434"

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

async def _ollama_embed(text: str) -> list[float] | None:
    """Ollama /api/embeddings. Returns None if unreachable / model missing."""
    if not text or not text.strip():
        return None
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_BASE, timeout=30) as client:
            resp = await client.post(
                "/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text[:8000]},
            )
            if resp.status_code == 404:
                logger.info(
                    "Embedding model %s not installed — run `ollama pull %s`",
                    EMBED_MODEL, EMBED_MODEL,
                )
                return None
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embedding")
            if not isinstance(vec, list) or len(vec) != EMBED_DIM:
                return None
            return [float(x) for x in vec]
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
    """Compute + store encrypted embedding for one note. Returns success flag."""
    vec = await _ollama_embed(text)
    if vec is None:
        return False

    dek = await get_user_dek(config, user_id)
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
        await db.commit()

    # Warm-cache writethrough: we already have the plaintext vector here,
    # so we can populate without paying another decrypt later. Evict
    # any stale entry first to keep the LRU honest.
    _cache_put(user_id, note_id, EMBED_MODEL, EMBED_DIM, vec)

    # Mark the note's embedding as fresh — clears the dirty flag set by
    # the content writer in store.py (best-effort; no-op if column missing).
    try:
        async with db_session(config) as db:
            await db.execute(
                "UPDATE notes SET embedding_dirty = 0 "
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
) -> dict:
    """Return ``{query, results, source}`` with top-k notes.

    ``source`` is ``"semantic"`` when the embedding path worked end-to-end,
    ``"substring"`` when we fell through to the substring index, or
    ``"empty"`` when the user has zero notes.

    ``tag_prefix`` (e.g. ``"topic/browser"``) filters the candidate set at
    the SQL layer before any embedding decryption happens — drops post-hoc
    waste from ~40% to ~0% for tag-scoped recalls.

    ``min_similarity`` drops weak hits whose cosine score falls below the
    threshold. Caller can pass ``0.0`` to keep the legacy behaviour.

    ``diversify`` runs MMR over the top ``3·k`` cosine-ranked candidates so
    the returned set isn't dominated by paraphrases of the same note. Set
    ``False`` for callers that explicitly want pure-cosine ordering
    (e.g. nearest-neighbour exemplar pulls).
    """
    q = (query or "").strip()
    if not q:
        return {"query": "", "results": [], "source": "empty"}

    q_vec = await _ollama_embed(q)
    vectors = (
        await _load_all(config, user_id, tag_substring=tag_prefix)
        if q_vec else []
    )

    if q_vec and vectors:
        # Keep vectors alongside scores so MMR can compute doc-doc similarity.
        scored: list[tuple[str, float, list[float]]] = [
            (nid, _cosine(q_vec, vec), vec) for nid, vec in vectors
        ]
        if min_similarity > 0:
            scored = [t for t in scored if t[1] >= min_similarity]
        scored.sort(key=lambda x: x[1], reverse=True)

        cap = max(1, min(50, k))
        if diversify and len(scored) > cap:
            # Widen pool to 3·k pre-rerank — gives MMR room to drop dups.
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

    # Fallback: substring search. The user never sees a hard error.
    hits = await store.search_notes(config, user_id, q, limit=k)
    return {
        "query": q,
        "results": hits,
        "source": "substring" if hits else "empty",
    }


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
    hammering a dead Ollama.
    """
    try:
        from lazyclaw.lazybrain import store as _lb_store
    except Exception:
        logger.debug("lazybrain store import failed in reindex_dirty_batch")
        return {"indexed": 0, "skipped": 0, "checked": 0}

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id FROM notes "
            "WHERE user_id = ? AND embedding_dirty = 1 "
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
            # Three failures in a row → Ollama is almost certainly down.
            # Bail out so we don't grind through the whole batch.
            if consecutive_skip >= 3 and indexed == 0:
                break

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
