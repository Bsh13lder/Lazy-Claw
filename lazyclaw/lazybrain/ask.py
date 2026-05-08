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
from typing import Any

from lazyclaw.config import Config
from lazyclaw.lazybrain import embeddings

logger = logging.getLogger(__name__)

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
