"""Per-user Aho-Corasick automaton cache for auto-link suggestions.

Aho-Corasick (Aho & Corasick, 1975) scans a body of text for a *dictionary*
of patterns in O(text length) regardless of how many patterns are in the
dictionary. This is the canonical answer to "find every page title that
appears verbatim in this draft" — it replaces the previous per-title regex
scan in :mod:`lazyclaw.lazybrain.autolink` which was O(N×M).

The automaton is built lazily per user from their note title list, cached
in-process, and invalidated by the same hook the wikilink injector uses
(see :mod:`lazyclaw.runtime.wikilink_injector` and
:func:`lazyclaw.lazybrain.store._schedule_post_save_hooks`).

Graceful degradation: when the ``ahocorasick`` C extension isn't installed
the cache returns ``None``; callers fall back to a deterministic substring
scan with the same filtering rules.
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

try:  # ahocorasick is the import name for pyahocorasick
    import ahocorasick  # type: ignore
    _HAS_AC = True
except Exception:  # pragma: no cover — install-time failure path
    ahocorasick = None  # type: ignore
    _HAS_AC = False

# Per-user cache: user_id → (titles_signature, automaton)
# The signature is a sorted tuple of (title_count, top-titles-prefix-hash)
# so we rebuild lazily when the title list materially changes. We don't
# track every keystroke — invalidate_cache(user_id) handles save events.
_AUTOMATA: dict[str, Any] = {}
_LOCK = Lock()


def is_available() -> bool:
    """True when the C extension is importable."""
    return _HAS_AC


def invalidate(user_id: str | None = None) -> None:
    """Drop the cached automaton for ``user_id`` (or every user when None)."""
    with _LOCK:
        if user_id is None:
            _AUTOMATA.clear()
        else:
            _AUTOMATA.pop(user_id, None)


def get_automaton(user_id: str, titles: list[str]) -> Any | None:
    """Return a built `ahocorasick.Automaton` for the user's title set.

    Cached in-process. Returns ``None`` when the C extension is missing —
    callers must handle that path. Title strings are added in *both* their
    raw + lowercase forms so the scanner can locate occurrences regardless
    of casing (we filter to canonical title at match time).

    The automaton is keyed off ``id(titles list)`` semantics: callers
    pass the current title list every call, but we rebuild only when the
    cached automaton's stored title-count differs. This is good enough for
    the post-save invalidation pattern; if the brain mutates titles in a
    way that doesn't fire ``invalidate``, the next save will refresh.
    """
    if not _HAS_AC or not titles:
        return None
    with _LOCK:
        cached = _AUTOMATA.get(user_id)
        if cached is not None:
            cached_count = getattr(cached, "_lb_title_count", -1)
            if cached_count == len(titles):
                return cached
        # (Re)build. Tiny — < 5 MB for 10 k titles.
        a = ahocorasick.Automaton()  # type: ignore[union-attr]
        # pyahocorasick.add_word(key, value) OVERWRITES on duplicate key.
        # Two titles that case-fold to the same key ("Upwork" + "upwork",
        # or the legacy "API" + new "Api") would silently collapse to
        # whichever appears LAST in iteration order. list_titles() returns
        # rows in updated_at DESC order, so we skip on second-and-later
        # sight to preserve the "most recent wins" rule used everywhere
        # else in the codebase (find_by_title, _reindex_links, etc.).
        seen_keys: set[str] = set()
        for title in titles:
            t = (title or "").strip()
            if not t or len(t) < 4:
                # Sub-4-char titles produce far too many false positives
                # ("API", "log", "id"); same threshold as the substring path.
                continue
            key = t.lower()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            # Store canonical title as the value; key is lowercase for the
            # scan itself. The scanner returns (end_idx, value) — value is
            # the canonical title we want to suggest.
            a.add_word(key, t)
        try:
            a.make_automaton()
        except Exception:  # pragma: no cover — corrupted state
            logger.debug("aho-corasick make_automaton failed", exc_info=True)
            return None
        # Stash count on the object so re-calls can check freshness cheaply.
        a._lb_title_count = len(titles)  # type: ignore[attr-defined]
        _AUTOMATA[user_id] = a
        return a


def scan(
    automaton: Any,
    text: str,
    *,
    masked_spans: list[tuple[int, int]] | None = None,
    max_suggestions: int = 8,
) -> list[dict]:
    """Scan ``text`` with ``automaton`` and return suggestion dicts.

    Filtering rules (matching the deterministic fallback in autolink.py):

    - Word boundaries on both sides — so "cat" doesn't hit inside
      "category".
    - Skip matches that overlap any range in ``masked_spans`` (callers
      pre-mask existing ``[[wikilinks]]``).
    - Longest match wins on overlap.
    - Cap at ``max_suggestions`` to keep UI noise down.

    Returns ``[{"text": <substring from draft>, "page": <canonical title>}]``.
    """
    if automaton is None or not text:
        return []
    masked = list(masked_spans or [])
    lower = text.lower()
    n = len(lower)

    raw: list[tuple[int, int, str]] = []  # (start, end, canonical_title)
    try:
        for end_idx, canonical in automaton.iter(lower):  # type: ignore[union-attr]
            # iter() yields (end_index_inclusive, value)
            length = len(canonical)
            start = end_idx - length + 1
            if start < 0:
                continue
            # word-boundary on left
            if start > 0:
                left = lower[start - 1]
                if left.isalnum() or left == "_":
                    continue
            # word-boundary on right
            if end_idx + 1 < n:
                right = lower[end_idx + 1]
                if right.isalnum() or right == "_":
                    continue
            # mask check
            if any(s < end_idx + 1 and start < e for s, e in masked):
                continue
            raw.append((start, end_idx + 1, canonical))
    except Exception:  # pragma: no cover
        logger.debug("aho-corasick scan failed", exc_info=True)
        return []

    if not raw:
        return []

    # Longest-match-wins: sort by length DESC then start ASC, sweep.
    raw.sort(key=lambda t: (-(t[1] - t[0]), t[0]))
    chosen_spans: list[tuple[int, int]] = []
    out: list[dict] = []
    for start, end, canonical in raw:
        if any(s < end and start < e for s, e in chosen_spans):
            continue
        out.append({"text": text[start:end], "page": canonical})
        chosen_spans.append((start, end))
        if len(out) >= max_suggestions:
            break
    # Stable sort by document order so the UI underlines feel natural.
    out.sort(key=lambda s: text.lower().find(s["text"].lower()))
    return out


__all__ = ["is_available", "invalidate", "get_automaton", "scan"]
