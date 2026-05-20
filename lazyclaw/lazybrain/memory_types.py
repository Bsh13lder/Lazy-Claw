"""Typed memory taxonomy for LazyBrain notes.

A ``memory_type`` is the *knowledge scope* of a note — what it represents
about the user / agent / project — and is **orthogonal** to ``Capture.kind``
in ``auto_capture.py`` which classifies *capture pattern* (decision/til/
price/deadline/recipe/url/contact/idea). One note has one of each:

- ``Capture.kind = "deadline"`` AND ``memory_type = "project"``
  → "Goal-X ships by Friday."
- ``Capture.kind = "til"``        AND ``memory_type = "user"``
  → "User prefers Sonnet over Opus for routine chat."
- ``Capture.kind = "decision"``   AND ``memory_type = "feedback"``
  → "User: stop summarizing at the end of every reply."

The taxonomy mirrors Claude Code's auto-memory types so the same mental
model applies in both surfaces. See MEMORY/feedback-claude-cache-amplifies-
stale-context — the reason this column exists is to give Claude+cache
routes a deterministic filter for "what belongs in the cached system
prompt vs. what must be fetched on-demand."

Two-column model:
    notes.memory_type       — knowledge scope (this module's taxonomy)
    notes.tags JSON         — free-form labels incl. Capture.kind values

Why a separate column instead of just another tag:
    - Indexable (auto-injection picks by ``memory_type IN (?, ?, ?, ?)``).
    - Required for the context-builder discipline that prevents stale
      session-log paraphrases from contaminating Claude's cached layer.
    - Tags are user-extensible; types are a closed enumeration.
"""

from __future__ import annotations

import re
from typing import Iterable


# Canonical typed-memory taxonomy. Closed set — extending requires a
# migration + classifier update.
MEMORY_TYPES: frozenset[str] = frozenset({
    "user",         # facts about the user (preferences, role, profile)
    "feedback",     # rules/corrections the user gave; "how to work with me"
    "project",      # state/decisions/scope for an ongoing project or goal
    "reference",    # pointers to external systems (URLs, dashboards, paths)
    "fact",         # general facts not tied to user/feedback/project/ref
    "session-log",  # paraphrased recaps of past sessions / channels (NOT
                    # auto-injected — query via recall_typed_memory skill)
    "other",        # unclassified; auto-classifier should normally avoid
})

# Subset that is safe to auto-inject into the system prompt every turn.
# Excluding ``session-log`` is the load-bearing decision — those entries
# are paraphrased dumps that competed with fresh tool data and produced
# the 2026-05-19 16:19 hallucination class. See MEMORY/project-f1-
# mechanical-enforcement.
AUTO_INJECT_TYPES: frozenset[str] = frozenset({
    "user", "feedback", "project", "reference",
})

# Default for notes that pre-date the column / for opaque callers. We bias
# to ``fact`` (not ``other``) so existing notes remain searchable and
# meaningful by default, just outside the auto-inject set.
DEFAULT_MEMORY_TYPE: str = "fact"


# ─── Classifier ───────────────────────────────────────────────────────────
#
# A note's ``memory_type`` is inferred at write time from a triple of signals
# (tags, content, title). The classifier is deterministic and cheap — no
# LLM call. Explicit ``memory_type`` argument always wins over inference.

_FEEDBACK_TAGS: frozenset[str] = frozenset({
    "feedback", "correction", "user-correction", "rule", "preference",
    "rules", "preferences", "behavior-rule",
})

_USER_TAGS: frozenset[str] = frozenset({
    "user", "profile", "about-me", "user-profile", "identity",
})

_PROJECT_TAGS: frozenset[str] = frozenset({
    "project", "goal", "plan", "decision", "architecture", "design",
    "scope", "milestone", "spec", "task", "deliverable",
})

_REFERENCE_TAGS: frozenset[str] = frozenset({
    "reference", "url", "link", "dashboard", "contact", "external-system",
    "bookmark", "doc-link", "tool-pointer",
})

_SESSION_LOG_TAGS: frozenset[str] = frozenset({
    "session-end", "daily-log", "weekly-summary", "session-log",
    "channel-thread", "conversation-recap", "thread-recap",
    "background_done", "background_failed", "background_started",
    # Daily journal pages — ``lazyclaw.lazybrain.journal`` stamps notes
    # with ``journal`` + ``journal/YYYY-MM-DD``. They're append-only
    # diaries full of paraphrased prior-turn content, so they belong
    # in the session-log scope (NOT auto-inject; queryable on-demand).
    "journal",
})


# ``journal/YYYY-MM-DD`` is a dynamic tag — every day produces a new
# value, so the static allowlist above can't enumerate it. This regex
# catches the dated form so the tag pass still classifies the page
# correctly on the first try.
_SESSION_LOG_TAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^journal/\d{4}-\d{2}-\d{2}$"),
)


# Phrase signals — first-line heuristics. Conservative (each phrase is
# specific enough that a false positive is rare). Order matters: the
# classifier walks tags first, then phrases, so explicit signals dominate.
_FEEDBACK_PHRASES: re.Pattern[str] = re.compile(
    r"^\s*(?:rule:|stop |never |always |don't |do not |from now on,|"
    r"user (?:wants|prefers|asked|told us))",
    re.IGNORECASE,
)

_PROJECT_PHRASES: re.Pattern[str] = re.compile(
    r"^\s*(?:goal:|project:|scope:|plan:|decision:|architecture:|"
    r"phase \d|milestone:|deliverable:)",
    re.IGNORECASE,
)

_REFERENCE_PHRASES: re.Pattern[str] = re.compile(
    r"^\s*(?:url:|link:|see\s+https?://|dashboard:|contact:|"
    r"endpoint:|repo:)",
    re.IGNORECASE,
)

_SESSION_LOG_PHRASES: re.Pattern[str] = re.compile(
    r"^\s*(?:##\s*last\s+(?:upwork|whatsapp|instagram|email|telegram)\b|"
    r"##\s*session (?:end|summary|recap)|"
    r"weekly summary|daily summary|"
    r"background (?:task|run) (?:done|failed|started)|"
    # Daily-journal title shapes: ``Journal — 2026-05-20`` (stub) and
    # ``2026-05-20 — Shipped F1 grounding`` (worker-LLM refreshed).
    # Caught here so that even if the journal tag is missing, the
    # title alone is enough to bucket the row as session-log.
    r"(?:#\s*)?Journal\s+—\s+\d{4}-\d{2}-\d{2}\b|"
    r"\d{4}-\d{2}-\d{2}\s+—\s+.+)",
    re.IGNORECASE,
)


def _normalize_tags(tags: Iterable[str] | None) -> set[str]:
    """Lowercase + dedup tag list (None-safe)."""
    if not tags:
        return set()
    return {t.strip().lower() for t in tags if t}


def infer_memory_type(
    content: str,
    *,
    tags: Iterable[str] | None = None,
    title: str | None = None,
) -> str:
    """Classify a note into one of :data:`MEMORY_TYPES`.

    Resolution order (first hit wins):
      1. Tags match a typed allowlist (``_FEEDBACK_TAGS``, ``_USER_TAGS``,
         ``_PROJECT_TAGS``, ``_REFERENCE_TAGS``, ``_SESSION_LOG_TAGS``).
      2. Title or first line matches a phrase regex for the same scope.
      3. Fallback to :data:`DEFAULT_MEMORY_TYPE` (``fact``).

    The classifier never raises — pass empty / None freely.
    """
    tag_set = _normalize_tags(tags)

    # Tag-based signals dominate — they're the explicit author signal.
    if tag_set & _FEEDBACK_TAGS:
        return "feedback"
    if tag_set & _USER_TAGS:
        return "user"
    if tag_set & _PROJECT_TAGS:
        return "project"
    if tag_set & _REFERENCE_TAGS:
        return "reference"
    if tag_set & _SESSION_LOG_TAGS:
        return "session-log"
    # Dynamic ``journal/YYYY-MM-DD`` tag — checked alongside the static
    # allowlist so the journal-page write path classifies on its first
    # save (no backfill round trip needed).
    if any(
        pat.match(tag)
        for tag in tag_set
        for pat in _SESSION_LOG_TAG_PATTERNS
    ):
        return "session-log"

    # Phrase signals — check the title first (richest), then the first
    # line of content. Anything past the first line is too noisy.
    head_candidates: list[str] = []
    if title:
        head_candidates.append(title)
    if content:
        head_candidates.append(content.strip().splitlines()[0] if content.strip() else "")

    for head in head_candidates:
        if not head:
            continue
        # Session-log first — its markers are the most specific (e.g.
        # "## Last Upwork Conversation"), so we don't want feedback /
        # project phrases inside a session dump to mis-classify.
        if _SESSION_LOG_PHRASES.search(head):
            return "session-log"
        if _FEEDBACK_PHRASES.search(head):
            return "feedback"
        if _PROJECT_PHRASES.search(head):
            return "project"
        if _REFERENCE_PHRASES.search(head):
            return "reference"

    return DEFAULT_MEMORY_TYPE


def is_auto_inject_type(memory_type: str | None) -> bool:
    """True if ``memory_type`` is safe to auto-inject into the system prompt.

    ``None`` and unknown values are treated as ``fact`` (not auto-inject) —
    fail closed so a row with NULL ``memory_type`` from a pre-migration
    write path doesn't accidentally contaminate the cached layer.
    """
    if memory_type is None:
        return False
    return memory_type in AUTO_INJECT_TYPES


def normalize_memory_type(memory_type: str | None) -> str:
    """Coerce ``memory_type`` to a valid value or :data:`DEFAULT_MEMORY_TYPE`.

    Used at write time so a typo'd kwarg from a caller doesn't poison the
    column. Case-insensitive on input; canonical lowercase on output.
    """
    if not memory_type:
        return DEFAULT_MEMORY_TYPE
    candidate = memory_type.strip().lower().replace("_", "-")
    if candidate in MEMORY_TYPES:
        return candidate
    return DEFAULT_MEMORY_TYPE
