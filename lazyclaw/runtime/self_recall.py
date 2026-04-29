"""Knowledge-gap detection + auto-recall from LazyBrain / personal memory.

Triggered when the brain is about to emit a text-only answer that contains
confusion phrases ("I don't know", "could you tell me", "what is your") or
is asking the user something it asked one or two turns earlier. Caller
injects the returned recall block as a system message and re-prompts the
brain BEFORE the answer is committed to the user.

Pure detector + async memory builder. No new abstractions: reuses the
existing LazyBrain semantic search and personal_memory store.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)


_GAP_PHRASES: tuple[str, ...] = (
    "i don't know",
    "i do not know",
    "i'm not sure",
    "i am not sure",
    "could you tell me",
    "could you share",
    "what is your",
    "what's your",
    "i don't have",
    "i do not have",
    "no information about",
    "can you remind me",
    "i'm not aware",
    "i am not aware",
    "i don't recall",
    "do not recall",
    "could you provide",
    "please tell me",
    "what was your",
    "remind me of",
)

# We only fire when one of those phrases appears in the LAST sentence of the
# brain's answer. Mid-sentence "I don't know if X is true, let me check" is
# fine — that's the brain reasoning, not asking the user.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Stopwords for keyword-overlap ranking (EN-only is fine — Spanish memories
# still match by their English-side terms in the user's question).
_STOPWORDS = frozenset((
    "a an the of for to in on at by with from is are was were be been being "
    "do does did have has had i you he she it we they me him her us them my "
    "your his their our its this that these those and or but if so than then "
    "what which who whom whose where when why how can could should would "
    "tell me know about please thanks thank just only also already still"
).split())


def _last_sentence(text: str) -> str:
    """Return the trailing sentence (or whole text if unsplittable)."""
    if not text:
        return ""
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    return parts[-1] if parts else text.strip()


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords removed, length>=3."""
    if not text:
        return []
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9'_-]{2,}", text.lower())
    return [t for t in toks if t not in _STOPWORDS]


def _repeats_recent_question(
    user_message: str, prior_user_messages: Iterable[str],
) -> bool:
    """The current user message looks like a question they already asked."""
    cur = (user_message or "").strip().lower()
    if "?" not in cur and not cur.startswith(("what", "where", "when", "who", "how", "why")):
        return False
    cur_tokens = set(_tokenize(cur))
    if len(cur_tokens) < 3:
        return False
    for prev in prior_user_messages:
        prev_tokens = set(_tokenize(prev or ""))
        if not prev_tokens:
            continue
        # Jaccard ≥ 0.6 on content tokens → same question asked recently.
        overlap = len(cur_tokens & prev_tokens)
        union = len(cur_tokens | prev_tokens)
        if union and overlap / union >= 0.6:
            return True
    return False


def detect_knowledge_gap(
    assistant_text: str,
    user_message: str,
    prior_user_messages: list[str],
) -> tuple[bool, str]:
    """Pure detector. Returns ``(is_gap, query)``.

    Triggers on either:
    - A gap phrase in the *last sentence* of ``assistant_text``, OR
    - The user repeating the same question they asked 1-2 turns earlier
      (signal that the brain hasn't satisfied them).

    ``query`` is the best string to feed into ``build_recall_block`` —
    typically the user message (it represents what the user actually
    wanted to know).
    """
    if not (assistant_text or "").strip():
        return False, ""

    last = _last_sentence(assistant_text).lower()
    phrase_hit = any(p in last for p in _GAP_PHRASES)

    repeat_hit = _repeats_recent_question(user_message, prior_user_messages)

    if not (phrase_hit or repeat_hit):
        return False, ""

    # Query: prefer the user message (what they want), fall back to last
    # sentence of the assistant (what the brain was confused about).
    query = (user_message or "").strip() or last
    return True, query


async def build_recall_block(
    config, user_id: str, gap_query: str,
) -> str | None:
    """Async. Run semantic_search + personal_memory overlap.

    Returns a markdown system block ready to inject, or ``None`` when there
    are zero hits (don't pollute the prompt with an empty section). Never
    raises — Ollama-down / DB errors degrade to ``None``.
    """
    if not (gap_query or "").strip():
        return None

    parts: list[str] = []

    # Lane 1: LazyBrain semantic search (k=5)
    try:
        from lazyclaw.lazybrain.embeddings import semantic_search

        sem = await semantic_search(
            config, user_id, gap_query, k=5, min_similarity=0.0,
        )
        results = sem.get("results") or []
        if results:
            parts.append("### From your second brain (LazyBrain)")
            for n in results[:5]:
                title = (n.get("title") or "(untitled)").strip()
                snippet = (n.get("content") or "").strip().splitlines()[0][:160]
                parts.append(f"- **{title}** — {snippet}")
    except Exception:
        logger.debug("self_recall: semantic_search failed", exc_info=True)

    # Lane 2: personal_memory keyword overlap (top 5)
    try:
        from lazyclaw.memory.personal import get_memories

        pool = await get_memories(config, user_id, limit=40)
        q_tokens = set(_tokenize(gap_query))
        if pool and q_tokens:
            scored: list[tuple[int, dict]] = []
            for m in pool:
                m_text = (m.get("content") or "").strip()
                if not m_text:
                    continue
                m_tokens = set(_tokenize(m_text))
                overlap = len(q_tokens & m_tokens)
                if overlap >= 1:
                    scored.append((overlap, m))
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:5]
            if top:
                parts.append("### From your personal memory")
                for _, m in top:
                    snippet = (m.get("content") or "").strip().splitlines()[0][:200]
                    parts.append(f"- {snippet}")
    except Exception:
        logger.debug("self_recall: get_memories failed", exc_info=True)

    if not parts:
        return None

    header = (
        "## Knowledge gap recall\n"
        "Before answering, you previously hesitated or asked the user about "
        "something. The user's own data below is likely the answer. "
        "Use it directly — do NOT ask the user to repeat themselves.\n"
    )
    return header + "\n".join(parts)
