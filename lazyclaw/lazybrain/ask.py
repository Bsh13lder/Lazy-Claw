"""RAG over the user's second brain: answer a question with [[citations]].

Pipeline:
  1. Run :func:`semantic_search` to pick the top-k most relevant notes.
  2. Feed their titles + excerpts into the brain LLM.
  3. Return a grounded markdown answer; the brain is instructed to cite
     every claim with ``[[Note Title]]`` so the user can click through.

Falls back to substring retrieval if Ollama + nomic-embed-text isn't set
up (embeddings module handles that transparently).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from lazyclaw.config import Config
from lazyclaw.lazybrain import embeddings

logger = logging.getLogger(__name__)

# Feature flag — env override lets the user revert to vector-only if the
# 1-hop graph expansion regresses on their vault. Default ON; "0", "false",
# "no", "off" disable. Memory: user wants every MiniMax-era behaviour to
# remain reachable via an escape hatch.
_GRAPH_EXPAND_ENV = "LAZYBRAIN_GRAPH_EXPAND"
_GRAPH_EXPAND_OFF = frozenset({"0", "false", "no", "off", ""})

# How many top vector hits seed the 1-hop expansion. Research (this
# session's fan-out) shows top-5 is the sweet spot — wider seeds add
# noise on PKM graphs without lifting recall.
_GRAPH_SEED_TOPK = 5


def _graph_expand_enabled() -> bool:
    return os.environ.get(_GRAPH_EXPAND_ENV, "1").strip().lower() not in _GRAPH_EXPAND_OFF


async def _expand_with_graph(
    config: Config,
    user_id: str,
    vec_results: list[dict],
    *,
    limit: int,
) -> list[dict]:
    """Fuse 1-hop wikilink neighbours of the top-K vector hits with the
    vector ranking via RRF. Returns the fused list of note dicts.

    Behaviour rules (per task spec):
    - Empty hop_hits → return vec_results unchanged (no crash).
    - ``graph.get_neighbors`` raising → log warning, return vec_results.
    - Works BEFORE and AFTER the parallel schema agent ships
      ``idx_note_links_to_note`` — the try/except is the contract.
    """
    if not vec_results:
        return vec_results

    # Lazy imports keep the ask path light when the flag is off.
    from lazyclaw.lazybrain import graph as _graph
    from lazyclaw.lazybrain import rrf as _rrf
    from lazyclaw.lazybrain import store as _store

    vec_ids = [n["id"] for n in vec_results if n.get("id")]
    if not vec_ids:
        return vec_results

    # Walk 1-hop neighbours of the top-K seeds. In-order dedupe so each
    # neighbour's first appearance fixes its RRF rank.
    hop_hits: list[str] = []
    seen: set[str] = set(vec_ids)  # seeds shouldn't re-count as neighbours
    for seed in vec_ids[:_GRAPH_SEED_TOPK]:
        try:
            payload = await _graph.get_neighbors(
                config, user_id, seed, depth=1,
            )
        except Exception as exc:
            # Graph not initialised, missing index pre-migration, or any
            # other transient failure → fall through to vector-only. The
            # warning surfaces in logs without breaking /ask.
            logger.warning(
                "graph expansion failed for seed %s — falling back to "
                "vector-only: %s", seed, exc,
            )
            return vec_results
        for node in payload.get("nodes") or []:
            nid = node.get("id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            hop_hits.append(nid)

    if not hop_hits:
        # Top-K have no wikilinks — nothing to fuse, keep vector ranking.
        return vec_results

    fused_ids = _rrf.fuse_to_ids([vec_ids, hop_hits], limit=limit)

    # Re-attach note dicts. Vector hits already carry content + _score; for
    # graph-only hits we hydrate from the store. None drops silently so a
    # stale link can't break the answer.
    vec_by_id = {n["id"]: n for n in vec_results if n.get("id")}
    out: list[dict] = []
    for nid in fused_ids:
        if nid in vec_by_id:
            out.append(vec_by_id[nid])
            continue
        note = await _store.get_note(config, user_id, nid)
        if note:
            out.append(note)
    return out


_PROMPT = """You are answering a question grounded in the user's personal notes.

Question:
{question}

Relevant notes (most relevant first):

{corpus}

Write a concise markdown answer to the question.

Rules:
- Use only information present in the notes above; never invent.
- After every factual sentence cite the source as [[Note Title]].
- If the notes don't contain the answer, say so explicitly instead of guessing.
- Keep it under 300 words."""


async def ask_notes(
    config: Config,
    user_id: str,
    question: str,
    *,
    k: int = 8,
) -> dict[str, Any]:
    """Return ``{question, answer, sources, source_count}``."""
    q = (question or "").strip()
    if not q:
        return {
            "question": q,
            "answer": "",
            "sources": [],
            "source_count": 0,
        }

    # Retrieve with semantic_search (auto-falls-back to substring + BM25).
    # When the user has opted into the reranker we widen the retrieval pool
    # by 2× and let the cross-encoder pick the final top-k — gives the rerank
    # pass meaningful options instead of just re-shuffling the top-k.
    from lazyclaw.lazybrain import rerank as _rerank
    rerank_enabled = await _rerank.is_enabled(config, user_id)
    retrieval_k = max(k, k * 2) if rerank_enabled else k
    retrieval = await embeddings.semantic_search(
        config, user_id, q, k=retrieval_k,
    )
    results = retrieval.get("results") or []
    retrieval_source = retrieval.get("source", "none")

    # 1-hop graph expansion (Phase: graph+RRF). Fuses wikilink neighbours
    # of the top-5 vector hits into the ranking via RRF so associative
    # queries surface notes the dense embedding alone would miss
    # (~20-25% recall lift on PKM graphs per this session's research).
    # Gated by ``LAZYBRAIN_GRAPH_EXPAND`` env (default "1"). Failures
    # degrade silently to vector-only.
    if results and _graph_expand_enabled():
        before_ids = [n.get("id") for n in results]
        results = await _expand_with_graph(
            config, user_id, results, limit=retrieval_k,
        )
        if [n.get("id") for n in results] != before_ids:
            retrieval_source = f"{retrieval_source}+graph"

    if rerank_enabled and results:
        try:
            results = _rerank.rerank_results(q, results, top_k=k)
            retrieval_source = f"{retrieval_source}+rerank"
        except Exception as exc:
            logger.debug("rerank pass skipped: %s", exc)

    if not results:
        return {
            "question": q,
            "answer": "I couldn't find anything in your notes that touches on that.",
            "sources": [],
            "source_count": 0,
            "retrieval_source": retrieval_source,
        }

    excerpts: list[str] = []
    titles: list[str] = []
    for n in results:
        title = n.get("title") or "(untitled)"
        titles.append(title)
        body = (n.get("content") or "").strip()
        if len(body) > 600:
            body = body[:600] + "…"
        excerpts.append(f"### [[{title}]]\n{body}")
    corpus = "\n\n".join(excerpts)
    prompt = _PROMPT.format(question=q, corpus=corpus)

    from lazyclaw.llm.eco_router import EcoRouter, ROLE_BRAIN
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await eco.chat(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "You answer questions strictly from the user's own "
                        "notes. Cite every claim with [[Note Title]]."
                    ),
                ),
                LLMMessage(role="user", content=prompt),
            ],
            user_id=user_id,
            role=ROLE_BRAIN,
        )
    except Exception as exc:
        logger.warning("ask_notes brain call failed: %s", exc)
        return {
            "question": q,
            "answer": f"Brain LLM unavailable right now ({exc}).",
            "sources": titles,
            "source_count": len(titles),
            "retrieval_source": retrieval_source,
        }

    return {
        "question": q,
        "answer": (resp.content or "").strip(),
        "sources": titles,
        "source_count": len(titles),
        "retrieval_source": retrieval_source,
    }
