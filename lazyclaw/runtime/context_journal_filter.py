"""Inject-time guard for the cached LazyBrain section in the system prompt.

The system prompt embeds two LazyBrain surfaces ahead of any message:
pinned notes and today's journal. Both layers feed Anthropic's prompt
cache for the whole turn, so anything in them competes with fresh tool
results for the model's attention until the cache invalidates.

Today's journal (``Journal — YYYY-MM-DD``) is a rolling append-only log
that frequently contains paraphrased prior-turn content (channel recaps,
"what we decided today"). Re-injecting that into the cached layer is the
documented "Claude+cache amplifies stale context" failure mode — the
2026-05-19 16:19 hallucination came from exactly this surface. The cure:
keep the journal queryable on-demand via the recall skill, but exclude
it from the pre-cached layer.

Pinned notes are user-curated, so they're allowed in the cached layer
— but each pinned note still has to satisfy the typed-memory auto-inject
gate (``user | feedback | project | reference``). A pinned ``session-log``
or a pinned row whose ``memory_type`` is still ``NULL`` (backfill not yet
applied) fails closed and is excluded.

Both helpers are pure: they never mutate the input list and never raise.
"""

from __future__ import annotations

import logging
import re

from lazyclaw.lazybrain.memory_types import is_auto_inject_type

logger = logging.getLogger(__name__)


# ``Journal — 2026-05-20`` is the canonical auto-generated title from
# ``lazyclaw.lazybrain.journal.ensure_today_journal``. The append_journal
# title-refresh path can rewrite it into a descriptive phrase (e.g.
# ``2026-05-20 — Shipped LazyBrain redesign``) — the refreshed shape is
# also caught below. The match is anchored so a note that merely mentions
# "Journal — 2026-05-20" in its body is not mis-flagged.
_JOURNAL_TITLE_RE: re.Pattern[str] = re.compile(
    r"^(?:Journal\s+—\s+\d{4}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2}\s+—\s+.+)$",
)


def is_journal_title(title: str | None) -> bool:
    """True if ``title`` is the daily-journal title shape.

    Matches both:
      - ``Journal — 2026-05-20``               (stub / pre-LLM-refresh)
      - ``2026-05-20 — Shipped F1 grounding``  (worker-LLM refreshed)

    Returns ``False`` for ``None`` / empty / unrelated titles so the
    caller can use this as a drop-in predicate without None checks.
    """
    if not title:
        return False
    return bool(_JOURNAL_TITLE_RE.match(title.strip()))


def filter_pinned_for_cache(
    pinned: list[dict],
) -> tuple[list[dict], list[str]]:
    """Filter a pinned-notes list for the cached LazyBrain prompt section.

    Returns ``(kept, excluded_titles)`` where:

    * ``kept`` is a NEW list (input never mutated) holding only notes
      whose ``memory_type`` passes :func:`is_auto_inject_type` AND whose
      title is not a daily-journal title.
    * ``excluded_titles`` is the list of dropped note titles — handed
      back so the caller can DEBUG-log which notes were filtered.

    Failure modes — all fail closed, never raise:
      * ``memory_type`` is ``None`` (backfill not yet applied) → excluded.
      * ``memory_type`` is ``session-log`` / ``fact`` / ``other`` /
        unknown → excluded.
      * Title matches :func:`is_journal_title` even if ``memory_type``
        somehow passes — defense in depth against future code paths
        pinning a journal page.
    """
    kept: list[dict] = []
    excluded_titles: list[str] = []
    for note in pinned or ():
        title = (note.get("title") or "").strip()
        memory_type = note.get("memory_type")
        if is_journal_title(title):
            excluded_titles.append(title or "(untitled)")
            continue
        if not is_auto_inject_type(memory_type):
            excluded_titles.append(title or "(untitled)")
            continue
        kept.append(note)
    return kept, excluded_titles


def should_inject_journal(journal: dict | None) -> bool:
    """True iff today's journal note is safe to inject into the cached layer.

    Always returns ``False`` for the canonical journal title shape — those
    pages are session-log paraphrases by construction and must be fetched
    on-demand via the recall skill, not pre-cached.

    Returns ``False`` for ``None`` / empty content so the caller can
    avoid an extra existence check.
    """
    if not journal:
        return False
    if not (journal.get("content") or "").strip():
        return False
    # Defense-in-depth: even if the journal's memory_type were marked
    # auto-inject by some future migration, the title shape alone is
    # enough to keep it out of the cached layer.
    return not is_journal_title(journal.get("title"))
