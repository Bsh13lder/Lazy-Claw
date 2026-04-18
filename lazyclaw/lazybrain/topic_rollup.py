"""Brain-LLM rollup over every note that touches a topic.

Given a topic (typically a page title or tag), gather:
  - all backlinks to that title (from ``note_links``)
  - all substring hits from ``search_notes``
Pass the merged set to the brain LLM to synthesise a structured rollup
with citations as ``[[wikilinks]]`` back to the source notes.

Cheap-ish: one brain call. The user invokes it explicitly via the
``lazybrain_topic_rollup`` skill or the "🧠 Ask about this page" button,
so cost is opt-in per-topic.
"""
from __future__ import annotations

import logging
from typing import Any

from lazyclaw.config import Config
from lazyclaw.lazybrain import store

logger = logging.getLogger(__name__)

_PROMPT = """You are summarising what the user knows about a topic.

Topic: {topic}

Here are excerpts from their notes touching this topic (most recent first):

{corpus}

Write a structured markdown rollup with these sections:
### Summary
One short paragraph.

### Decisions
Bullet list of concrete decisions the user has recorded. Cite each with
[[Exact Note Title]] where the decision lives.

### Open questions
Bullet list of questions still unresolved, again citing [[Note Title]].

### Sources
Bullet list of the notes you referenced, each as [[Note Title]] — one per line.

Rules:
- Use ONLY information from the excerpts above; never invent.
- If a section has no content, write "(none yet)" under it.
- Keep the whole rollup under 450 words."""


async def topic_rollup(
    config: Config,
    user_id: str,
    topic: str,
) -> dict[str, Any]:
    """Return ``{topic, rollup, sources, source_count}``.

    ``rollup`` is the markdown body from the brain LLM. ``sources`` is a
    deduped list of the note titles that went into the context."""
    if not topic or not topic.strip():
        return {
            "topic": topic,
            "rollup": "",
            "sources": [],
            "source_count": 0,
            "error": "empty topic",
        }

    topic = topic.strip()

    # 1. Gather the corpus: backlinks + substring hits, deduped by note id.
    backlinked = await store.get_backlinks(config, user_id, topic)
    searched = await store.search_notes(config, user_id, topic, limit=30)
    by_id: dict[str, dict] = {}
    for n in list(backlinked) + list(searched):
        by_id.setdefault(n["id"], n)

    corpus_notes = sorted(
        by_id.values(),
        key=lambda n: n.get("updated_at") or n.get("created_at") or "",
        reverse=True,
    )[:18]

    if not corpus_notes:
        return {
            "topic": topic,
            "rollup": f"No notes reference _{topic}_ yet.",
            "sources": [],
            "source_count": 0,
        }

    excerpts: list[str] = []
    titles: list[str] = []
    for n in corpus_notes:
        title = n.get("title") or "(untitled)"
        titles.append(title)
        body = (n.get("content") or "").strip()
        body = body[:600] + ("…" if len(body) > 600 else "")
        excerpts.append(f"### [[{title}]]\n{body}")
    corpus = "\n\n".join(excerpts)

    prompt = _PROMPT.format(topic=topic, corpus=corpus)

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
                        "You synthesise personal note rollups grounded in the "
                        "user's own excerpts. Cite with [[wikilinks]]."
                    ),
                ),
                LLMMessage(role="user", content=prompt),
            ],
            user_id=user_id,
            role=ROLE_BRAIN,
        )
    except Exception as exc:
        logger.warning("topic rollup brain call failed: %s", exc)
        return {
            "topic": topic,
            "rollup": f"Rollup unavailable — LLM call failed ({exc})",
            "sources": titles,
            "source_count": len(titles),
        }

    return {
        "topic": topic,
        "rollup": (resp.content or "").strip(),
        "sources": titles,
        "source_count": len(titles),
    }
