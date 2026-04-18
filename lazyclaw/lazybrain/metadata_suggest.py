"""Worker-LLM suggests a title + tags for a raw note draft.

Used when the user saves a note without either field. We never overwrite
silently — the UI surfaces suggestions as a toast and lets the user
Accept / Edit / Dismiss. The skill path is the same: return a shaped
response the agent can forward to the user.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

_PROMPT = """You are helping organise a user's personal note archive.

Here is the note content:
---
{text}
---

Propose:
1. A short title (2–6 words, no trailing period).
2. Between 1 and 5 lowercase single-word hashtags that describe the topic.
   Prefer reusing common tags the user already has: {existing_tags}.

Output STRICT JSON of this shape, no prose, no fence:
{{"title": "Short title here", "tags": ["tag1", "tag2"]}}

If the note is too thin to classify, return {{"title": "", "tags": []}}."""


async def suggest_metadata(
    config: Config,
    user_id: str,
    content: str,
    existing_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return ``{title, tags, source}`` where source is ``llm`` or ``none``."""
    if not content or len(content.strip()) < 12:
        return {"title": "", "tags": [], "source": "none"}

    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    tag_hint = ", ".join(f"#{t}" for t in (existing_tags or [])[:40])
    prompt = _PROMPT.format(text=content[:2200], existing_tags=tag_hint or "(none yet)")

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await eco.chat(
            messages=[
                LLMMessage(role="system", content="You output JSON only."),
                LLMMessage(role="user", content=prompt),
            ],
            user_id=user_id,
            role=ROLE_WORKER,
        )
    except Exception as exc:
        logger.debug("metadata suggest LLM failed: %s", exc)
        return {"title": "", "tags": [], "source": "none"}

    raw = (resp.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"title": "", "tags": [], "source": "none"}

    title = str(data.get("title") or "").strip()[:120]
    tags_raw = data.get("tags")
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            s = str(t or "").strip().lstrip("#").lower()
            if s and len(s) <= 40 and s not in tags:
                tags.append(s)
            if len(tags) >= 5:
                break

    return {"title": title, "tags": tags, "source": "llm" if (title or tags) else "none"}
