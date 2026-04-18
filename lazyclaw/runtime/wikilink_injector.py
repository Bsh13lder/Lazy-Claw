"""Auto-linkifier: rewrite agent responses so existing LazyBrain page names
become ``[[wikilinks]]`` — the graph grows itself just by the agent talking.

Two guardrails keep this from hallucinating links:

- **Exact case match only** — "redis" matches a note titled "redis", not "Redis".
- **Skip code fences + existing `[[...]]`** — we never rewrite inside code
  blocks or already-linked text.

Behind a simple 30-second per-user LRU cache so we don't hit the store on
every single response.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from threading import Lock

from lazyclaw.config import Config
from lazyclaw.lazybrain import store

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0


@dataclass
class _Cached:
    titles: set[str]
    fetched_at: float


_cache: dict[str, _Cached] = {}
_lock = Lock()

# Fenced + inline code — stripped from the rewrite surface
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_EXISTING_LINK_RE = re.compile(r"\[\[[^\]]+\]\]")


async def _titles_for(config: Config, user_id: str) -> set[str]:
    now = time.monotonic()
    with _lock:
        entry = _cache.get(user_id)
        if entry and (now - entry.fetched_at) < _CACHE_TTL:
            return entry.titles
    titles = set(await store.list_titles(config, user_id, limit=500))
    with _lock:
        _cache[user_id] = _Cached(titles=titles, fetched_at=now)
    return titles


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Intervals that must never be rewritten (code + existing wikilinks)."""
    spans: list[tuple[int, int]] = []
    for m in _FENCE_RE.finditer(text):
        spans.append((m.start(), m.end()))
    for m in _INLINE_CODE_RE.finditer(text):
        spans.append((m.start(), m.end()))
    for m in _EXISTING_LINK_RE.finditer(text):
        spans.append((m.start(), m.end()))
    spans.sort()
    return spans


def _in_protected(pos: int, spans: list[tuple[int, int]]) -> bool:
    for start, end in spans:
        if start <= pos < end:
            return True
        if pos < start:
            return False
    return False


async def inject(
    config: Config,
    user_id: str,
    text: str,
    *,
    max_rewrites: int = 20,
) -> str:
    """Return ``text`` with known page titles rewrapped as ``[[wikilinks]]``."""
    if not text or "[[" in text and "]]" in text and len(text) < 200:
        # Short messages already containing wikilinks → skip
        pass

    try:
        titles = await _titles_for(config, user_id)
    except Exception:
        logger.debug("wikilink_injector title fetch failed", exc_info=True)
        return text
    if not titles:
        return text

    protected = _protected_spans(text)
    # Longest titles first — avoid over-matching shorter substrings
    sorted_titles = sorted(titles, key=len, reverse=True)

    rewrites = 0
    out = text
    already_done: set[str] = set()
    for title in sorted_titles:
        if rewrites >= max_rewrites:
            break
        if len(title) < 3:
            continue
        pattern = re.compile(r"\b" + re.escape(title) + r"\b")
        new_parts: list[str] = []
        cursor = 0
        found_any = False
        for m in pattern.finditer(out):
            if _in_protected(m.start(), protected):
                continue
            # Exact case match only (strict guardrail)
            if m.group(0) != title and m.group(0).lower() != title:
                continue
            found_any = True
            new_parts.append(out[cursor : m.start()])
            new_parts.append(f"[[{m.group(0)}]]")
            cursor = m.end()
            rewrites += 1
            if rewrites >= max_rewrites:
                break
        if not found_any:
            continue
        new_parts.append(out[cursor:])
        out = "".join(new_parts)
        already_done.add(title)
        # Protected spans just shifted — recompute cheaply
        protected = _protected_spans(out)

    return out


def invalidate_cache(user_id: str | None = None) -> None:
    """Drop cached titles. Call after mass note insert/delete."""
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(user_id, None)
