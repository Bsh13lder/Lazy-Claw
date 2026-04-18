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

    # Retrieve with semantic_search (auto-falls-back to substring).
    retrieval = await embeddings.semantic_search(config, user_id, q, k=k)
    results = retrieval.get("results") or []
    retrieval_source = retrieval.get("source", "none")

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
