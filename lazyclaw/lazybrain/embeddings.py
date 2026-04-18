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
    return True


async def delete_embedding(config: Config, note_id: str) -> None:
    async with db_session(config) as db:
        await db.execute(
            "DELETE FROM note_embeddings WHERE note_id = ?", (note_id,)
        )
        await db.commit()


async def _load_all(
    config: Config, user_id: str
) -> list[tuple[str, list[float]]]:
    """Decrypt + unpack every vector for this user."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT note_id, model, dim, vector FROM note_embeddings "
            "WHERE user_id = ? AND model = ? AND dim = ?",
            (user_id, EMBED_MODEL, EMBED_DIM),
        )
        data = await rows.fetchall()

    out: list[tuple[str, list[float]]] = []
    for note_id, _model, dim, enc_vec in data:
        try:
            hex_blob = decrypt_field(enc_vec, dek, _emb_aad(user_id), fallback="")
            if not hex_blob:
                continue
            blob = bytes.fromhex(hex_blob)
            vec = _unpack(blob, int(dim))
            out.append((note_id, vec))
        except Exception:
            continue
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
) -> dict:
    """Return ``{query, results, source}`` with top-k notes.

    ``source`` is ``"semantic"`` when the embedding path worked end-to-end,
    ``"substring"`` when we fell through to the substring index, or
    ``"empty"`` when the user has zero notes."""
    q = (query or "").strip()
    if not q:
        return {"query": "", "results": [], "source": "empty"}

    q_vec = await _ollama_embed(q)
    vectors = await _load_all(config, user_id) if q_vec else []

    if q_vec and vectors:
        scored = [
            (nid, _cosine(q_vec, vec)) for nid, vec in vectors
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[: max(1, min(50, k))]
        results: list[dict] = []
        for nid, score in top:
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
    "semantic_search",
    "reindex_user",
    "upsert_embedding",
    "ensure_embedding",
    "delete_embedding",
]
