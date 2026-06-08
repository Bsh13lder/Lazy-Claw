"""Inject-time guards for the cached LazyBrain section AND the
conversation-history view passed to the LLM.

There are two contamination surfaces this module closes:

1. **Pre-cached LazyBrain section.** The system prompt embeds two
   LazyBrain layers ahead of any message: pinned notes and today's
   journal. Both feed Anthropic's prompt cache for the whole turn, so
   anything in them competes with fresh tool results for the model's
   attention until the cache invalidates.

   - Today's journal (``Journal — YYYY-MM-DD``) is a rolling append-only
     log that frequently contains paraphrased prior-turn content
     (channel recaps, "what we decided today"). Re-injecting it into the
     cached layer is the documented "Claude+cache amplifies stale
     context" failure mode — the 2026-05-19 16:19 hallucination came
     from exactly this surface. The cure: keep the journal queryable
     on-demand via the recall skill, but exclude it from the pre-cached
     layer.
   - Pinned notes are user-curated, so they're allowed in the cached
     layer — but each pinned note still has to satisfy the typed-memory
     auto-inject gate (``user | feedback | project | reference``). A
     pinned ``session-log`` or a pinned row whose ``memory_type`` is
     still ``NULL`` (backfill not yet applied) fails closed and is
     excluded.

2. **Polluted assistant history.** Every accepted assistant reply is
   persisted, including F1-degraded-accept replies that ship after
   retry exhaustion. The next turn loads that reply as durable history
   and the brain mimics it — producing the same hallucinated quote
   shape (e.g. ``**James Blue (9:12 PM):** Mac [[computer]]``) day
   after day even though tool data is correct. The pop-draft helper in
   ``agent.py`` only sanitizes the in-flight draft; it can't reach
   yesterday's persisted assistant rows.

   :func:`quarantine_polluted_history` is the durable defense: it
   scans the recent assistant slice of the history list AFTER
   decryption and BEFORE the messages go to the LLM, and replaces any
   message whose content shows memory-leak signals with a quarantine
   placeholder. The DB row is never touched — this is an in-memory
   view only.

All helpers are pure: they never mutate the input lists and never
raise (defensive try/except inside the scan loop).
"""

from __future__ import annotations

import logging
import re

from lazyclaw.lazybrain.memory_types import is_auto_inject_type
from lazyclaw.runtime.f1_confabulation_detector import (
    _find_wikilink_in_quote_block,
)
from lazyclaw.runtime.f1_content_verifier import (
    parse_quote_lines,
    verify_quotes_against_tool_results,
)

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


# ─── Polluted-history quarantine ─────────────────────────────────────────

# How many trailing messages to scan for memory-leak signals. Older
# messages are already collapsed by the sliding-window summarizer, so
# scanning further back is wasted CPU on the hot context-build path.
# 20 covers ~6 user/assistant exchanges with tool-call interleaving —
# enough to catch yesterday's polluted replies that survived into the
# uncompressed window.
_QUARANTINE_SCAN_LIMIT: int = 20

# Replacement body for a quarantined assistant message. Kept terse and
# unambiguous: the brain must see this as a sanitized stub, NOT as a
# template to mimic. The bracket markers mirror the SYSTEM injection
# convention already used by the F1 confabulation retry path.
_QUARANTINE_PLACEHOLDER: str = (
    "[QUARANTINED — flagged hallucination from prior turn "
    "(wikilink leak or unverified quotes); do not use as template]"
)

# Majority threshold for "unverified quotes => quarantine". Mirrors the
# rule in ``detect_confabulation``: a single near-miss quote is often
# normalization drift, but if half-or-more of the quote lines don't
# trace to fresh tool data, the assistant message was confabulating.
def _is_majority_unverified(verified_count: int, total: int) -> bool:
    if total <= 0:
        return False
    unverified = total - verified_count
    return unverified > 0 and unverified >= max(1, total // 2)


def _quarantine_reason(
    content: str,
    tool_results_this_turn: list[str] | None,
) -> tuple[str, str] | None:
    """Return ``(reason, evidence)`` if ``content`` is polluted, else None.

    Pure helper. Two checks, in order of cost:

    1. **Wikilink-in-quote** (always-on, ~free): reuses the public
       :func:`_find_wikilink_in_quote_block` from the F1 detector.
       Real contacts never send ``[[wikilink]]`` syntax inside a quote
       block — its presence proves the prior reply pulled content out
       of a lazybrain note and presented it as a live conversation.
    2. **Quote-mismatch** (only if ``tool_results_this_turn`` is given):
       parses ``> sender (ts): content`` lines and checks each one
       against this turn's fresh tool results. If a majority of quote
       lines don't trace to live data, the prior reply was inventing
       quotes — quarantine it.

    Returns ``None`` for clean / empty content. Defensive: every check
    is wrapped so a regex/parse failure on one message cannot crash
    the whole filter.
    """
    if not content:
        return None

    try:
        wl = _find_wikilink_in_quote_block(content)
        if wl:
            return ("wikilink", wl)
    except Exception:
        # A pathological prior reply must never crash context build.
        pass

    if tool_results_this_turn:
        try:
            quotes = parse_quote_lines(content)
            if quotes:
                report = verify_quotes_against_tool_results(
                    quotes, tool_results_this_turn,
                )
                if _is_majority_unverified(
                    report.verified_count, report.total_quotes,
                ):
                    evidence = (
                        f"{report.unverified_count}/{report.total_quotes}"
                        " quotes unverified"
                    )
                    return ("quote-mismatch", evidence)
        except Exception:
            pass

    return None


def quarantine_polluted_history(
    messages: list,
    tool_results_this_turn: list[str] | None = None,
    *,
    scan_limit: int | None = _QUARANTINE_SCAN_LIMIT,
) -> list:
    """Return a NEW message list with polluted assistant rows sanitized.

    Scans the LAST ``scan_limit`` (default ``_QUARANTINE_SCAN_LIMIT``)
    messages for assistant rows showing memory-leak signals (wikilinks
    inside quote blocks and, when ``tool_results_this_turn`` is given,
    majority-unverified quote lines). For each match, emits one message
    with the same ``role`` + metadata but with ``content`` replaced by
    a quarantine placeholder.

    Contract:

    * Input is never mutated. Returns a new list of new message
      objects for the quarantined slots and reuses the originals
      elsewhere — safe to pass straight into the LLM call.
    * Only ``role="assistant"`` messages are eligible for quarantine.
      ``user`` / ``tool`` / ``system`` rows pass through untouched.
    * Performance guard: messages older than the trailing
      ``scan_limit`` are skipped entirely UNLESS ``scan_limit`` is
      ``None`` (scan-everything mode). The post-compression call site
      uses the trailing-window default; the pre-split compressor call
      site passes ``scan_limit=None`` so polluted rows about to be
      baked into the summary get caught no matter how deep they are.
    * Logging: the FIRST quarantine on a turn logs at INFO; subsequent
      ones at DEBUG. Format documented below.

    The function accepts ``list[LLMMessage]`` in production but is
    typed as ``list`` so the unit tests can use plain dataclasses /
    dicts / simple stand-in objects without importing the provider
    module. Anything with ``.role`` and ``.content`` attributes works.
    """
    if not messages:
        return list(messages or ())

    out = list(messages)
    n = len(out)
    if scan_limit is None:
        scan_start = 0
    else:
        scan_start = max(0, n - scan_limit)
    first_logged = False

    for idx in range(scan_start, n):
        msg = out[idx]
        try:
            role = getattr(msg, "role", None)
            if role != "assistant":
                continue
            content = getattr(msg, "content", None) or ""
        except Exception:
            continue

        reason_pair = _quarantine_reason(content, tool_results_this_turn)
        if reason_pair is None:
            continue
        reason, evidence = reason_pair

        # Build a replacement with the SAME shape as the original. We
        # use the original class so downstream code (tool_call_id /
        # tool_calls handling) keeps working.
        try:
            replacement = type(msg)(
                role=msg.role,
                content=_QUARANTINE_PLACEHOLDER,
                tool_call_id=getattr(msg, "tool_call_id", None),
                tool_calls=None,
            )
        except TypeError:
            # Fallback for test stand-ins that don't accept all kwargs.
            try:
                replacement = type(msg)(role=msg.role, content=_QUARANTINE_PLACEHOLDER)
            except Exception:
                # Last resort: skip rather than crash the turn.
                continue
        out[idx] = replacement

        offending = (evidence or "")[:80]
        log_msg = (
            f"[history-filter] quarantined assistant message at index={idx} "
            f"— reason={reason}, offending={offending!r}"
        )
        if not first_logged:
            logger.info(log_msg)
            first_logged = True
        else:
            logger.debug(log_msg)

    return out


# ── Stale provider-error neutralization (2026-06-03) ──────────────────
#
# A tool that failed on a PRIOR turn (credit/billing, auth, rate-limit, 4xx/
# 5xx) is persisted as a ``role="tool"`` result and replayed on later turns.
# The brain reads it as a LIVE blocker and refuses to re-attempt even after
# the cause is fixed (the "credit balance too low" / ``req_…`` refusal loop),
# and it also gets baked into long-term summaries. Replacing the verbatim
# error with a neutral marker — in raw history AND before summarization —
# lets the brain re-attempt and judge from the live result.
#
# Canonical pattern + marker live here so ``agent.py`` (recent-window filter)
# and ``compressor.py`` (pre-summarization filter) share one source of truth.
STALE_PROVIDER_ERROR_RE = re.compile(
    r"(Sorry, an error occurred|Error code: [45]\d{2}|"
    r"authentication_error|invalid x-api-key|"
    r"AuthenticationError|rate_limit_error|"
    r"credit balance (?:is )?too low|Plans & Billing)",
    re.IGNORECASE,
)

STALE_TOOL_ERROR_MARKER = (
    "[a tool call errored on an EARLIER turn — that error is stale and may be "
    "resolved now (credit/provider/login state changes between turns). "
    "Re-attempt the tool and judge ONLY from the live result; do NOT cite this "
    "as a current blocker.]"
)


def is_stale_provider_error(text: str | None) -> bool:
    """True when *text* reads like a stale provider/credit/auth/rate-limit error."""
    return bool(text) and STALE_PROVIDER_ERROR_RE.search(text) is not None


def neutralize_stale_errors(messages: list) -> list:
    """Replace stale provider-error content (assistant OR tool rows) with a
    neutral marker so a PAST failure can't be read as a live blocker.

    Replace-not-drop: preserves ``role`` + ``tool_call_id`` so tool_use↔
    tool_result pairing and the compressor's tool-sequence split stay intact.
    Pure: never mutates the input list and never raises.
    """
    out = list(messages)
    for idx, msg in enumerate(out):
        try:
            content = getattr(msg, "content", None) or ""
        except Exception:
            continue
        if not is_stale_provider_error(content):
            continue
        try:
            replacement = type(msg)(
                role=getattr(msg, "role", "tool"),
                content=STALE_TOOL_ERROR_MARKER,
                tool_call_id=getattr(msg, "tool_call_id", None),
                tool_calls=None,
            )
        except TypeError:
            # Test stand-ins that don't accept all kwargs.
            try:
                replacement = type(msg)(
                    role=getattr(msg, "role", "tool"),
                    content=STALE_TOOL_ERROR_MARKER,
                )
            except Exception:
                continue
        out[idx] = replacement
        logger.debug(
            "[history-filter] neutralized stale provider error at index=%d (role=%s)",
            idx, getattr(msg, "role", "?"),
        )
    return out
