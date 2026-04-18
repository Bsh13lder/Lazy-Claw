"""Worker-LLM-powered auto-link suggestions for note drafts.

Given a draft text and the user's list of existing page titles, the worker
model proposes substrings that should become ``[[wikilinks]]`` — i.e. names
that appear verbatim in the text but aren't linked yet. The UI can show
these as ghost underlines and let the user accept one per click.

Cost: one worker-role LLM call per suggestion request. Cheap because the
worker is usually a local Ollama model (gemma4:e2b). Fire-and-forget at the
UI layer — never blocks typing.

Fallback: if the LLM is unavailable or returns garbage, we do a pure
substring match against plaintext title_keys. Good enough to still feel
"smart" and always works offline.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from lazyclaw.config import Config
from lazyclaw.lazybrain import store

logger = logging.getLogger(__name__)

_PROMPT = """You are an auto-linking assistant for a personal note app.

The user is writing this draft:
---
{text}
---

These pages already exist in their vault (one per line):
{titles}

Your job: identify substrings of the draft that refer to any existing page
above. A hit is valid only if the substring matches the page title
case-insensitively (subject to simple plural / possessive edits), appears
as a standalone noun phrase, and is NOT already inside `[[...]]`.

Return JSON of the form:
{{"suggestions": [{{"text": "exact substring from draft", "page": "existing page title"}}]}}

Rules:
- Maximum 8 suggestions.
- Skip common-word matches ("a", "the", "today").
- Skip anything already wrapped in double brackets.
- If nothing matches, return {{"suggestions": []}}.
- Output ONLY JSON. No prose, no markdown fence."""


def _pure_substring_suggestions(text: str, titles: list[str]) -> list[dict]:
    """Deterministic offline fallback: find verbatim occurrences of any title.

    Respects word boundaries and skips matches already inside ``[[...]]`` by
    masking wikilink spans first. Case-insensitive.
    """
    if not text or not titles:
        return []

    # Mask existing [[wikilinks]] so we never suggest a double-link.
    masked = re.sub(r"\[\[[^\[\]\n]+\]\]", lambda m: "\0" * len(m.group(0)), text)
    lower = masked.lower()

    # Sort titles longest-first so "New York City" wins over "New York".
    sorted_titles = sorted({t.strip() for t in titles if t and len(t) > 2}, key=len, reverse=True)

    seen_spans: list[tuple[int, int]] = []
    out: list[dict] = []
    for title in sorted_titles:
        needle = title.lower()
        # require word boundary on both sides so "cat" doesn't hit "category"
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])",
        )
        for m in pattern.finditer(lower):
            start, end = m.span()
            # skip if overlaps a mask (wikilink) or a previously matched span
            if any(s < end and start < e for s, e in seen_spans):
                continue
            if "\0" in masked[start:end]:
                continue
            out.append({"text": text[start:end], "page": title})
            seen_spans.append((start, end))
            if len(out) >= 8:
                return out
    return out


async def _llm_suggestions(
    config: Config,
    user_id: str,
    text: str,
    titles: list[str],
) -> list[dict] | None:
    """Ask the worker LLM for suggestions. Returns None on any failure."""
    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    # Cap the prompt so the worker model (often 2B local) doesn't choke.
    capped_text = text[:1800]
    capped_titles = "\n".join(f"- {t}" for t in titles[:180])
    prompt = _PROMPT.format(text=capped_text, titles=capped_titles)

    try:
        paid_router = LLMRouter(config)
        eco = EcoRouter(config, paid_router)
        resp = await eco.chat(
            messages=[
                LLMMessage(role="system", content="You output JSON only."),
                LLMMessage(role="user", content=prompt),
            ],
            user_id=user_id,
            role=ROLE_WORKER,
        )
    except Exception as exc:
        logger.debug("autolink worker LLM failed: %s", exc)
        return None

    raw = (resp.content or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("autolink LLM returned non-JSON: %r", raw[:200])
        return None

    suggestions = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(suggestions, list):
        return None

    # Validate each suggestion — the LLM occasionally hallucinates titles.
    title_lookup = {t.lower(): t for t in titles}
    clean: list[dict] = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        snippet = str(s.get("text", "")).strip()
        page = str(s.get("page", "")).strip()
        if not snippet or not page:
            continue
        # Must actually appear verbatim in the draft (case-insensitive).
        if snippet.lower() not in text.lower():
            continue
        canonical = title_lookup.get(page.lower())
        if not canonical:
            continue
        clean.append({"text": snippet, "page": canonical})
        if len(clean) >= 8:
            break
    return clean


async def suggest_links(
    config: Config,
    user_id: str,
    text: str,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Return auto-link suggestions for ``text``.

    Shape: ``{"suggestions": [{"text": str, "page": str}], "source": str}``

    ``source`` is ``"llm"`` when the worker model produced the suggestions,
    ``"substring"`` when we fell back to the deterministic match, or
    ``"none"`` when both paths produced zero hits.
    """
    if not text or not text.strip():
        return {"suggestions": [], "source": "none"}

    titles = await store.list_titles(config, user_id, limit=400)
    if not titles:
        return {"suggestions": [], "source": "none"}

    llm_suggestions: list[dict] | None = None
    if use_llm:
        llm_suggestions = await _llm_suggestions(config, user_id, text, titles)

    if llm_suggestions:
        return {"suggestions": llm_suggestions, "source": "llm"}

    fallback = _pure_substring_suggestions(text, titles)
    if fallback:
        return {"suggestions": fallback, "source": "substring"}
    return {"suggestions": [], "source": "none"}
