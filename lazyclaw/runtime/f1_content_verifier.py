"""F1 phase-2 content verifier (observation-only).

Phase-1 (structural) lives in ``agent._check_f1_violation`` and only
checks that ``> sender (ts): content`` quote lines EXIST in a reply. That
catches drafts where the brain skips quoting entirely, but does NOT catch
drafts where the brain quotes garbage (e.g. today's 13:34 incident: a bad
``room_id`` argument made ``upwork_get_conversation`` scrape an unrelated
DOM and the brain faithfully quoted the bad data).

Phase-2 closes that gap: it parses each quote line out of the reply and
checks whether the quoted ``content`` actually appears anywhere in the
fresh tool results from this turn. A quote that doesn't match any tool
output is "unverified" — meaning either the tool returned garbage, the
brain hallucinated, or the parser/normalization is too strict.

OBSERVATION-ONLY GUARDRAIL
==========================
``F1_PHASE2_ENFORCE`` is intentionally ``False`` at module-load time. When
False, the wiring in ``agent.py`` only emits a WARNING log on unverified
quotes — it does NOT block, re-prompt, or modify the reply. Flip to
``True`` once telemetry shows the verifier's false-positive rate is low
enough to enforce. Until then we want signal, not interruptions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, NamedTuple

logger = logging.getLogger(__name__)


# Flip to True ONLY after observation-mode telemetry shows the verifier
# isn't false-positiving on legitimate replies. Until then, the agent
# wiring logs unverified quotes but does NOT block the reply.
F1_PHASE2_ENFORCE: bool = False


# How many characters of a quote's ``content`` to use as the substring
# probe against tool_results. The full content can be hundreds of chars;
# the first ~80 chars are usually unique enough to identify a match
# without being so long that minor formatting drift breaks the lookup.
_PROBE_PREFIX_CHARS: int = 80

# Minimum probe length — quotes shorter than this are skipped (too short
# to be a meaningful claim, false-positive risk too high).
_MIN_PROBE_CHARS: int = 8


class QuoteTuple(NamedTuple):
    """One parsed quote line from a reply.

    Fields:
        sender: Text before the parenthesized timestamp (stripped).
        timestamp: Text inside the first ``(...)`` group (stripped).
        content: Everything after ``):`` to end-of-line (stripped).
        line_no: 1-based line number within the reply.
    """

    sender: str
    timestamp: str
    content: str
    line_no: int


@dataclass(frozen=True)
class VerificationReport:
    """Outcome of comparing parsed quotes against fresh tool results.

    Immutable so callers can safely log/forward without defensive copies.
    """

    total_quotes: int
    verified_count: int
    unverified_count: int
    unverified_quotes: tuple[tuple[QuoteTuple, str], ...]

    def summary(self) -> str:
        """One-line summary string suitable for log output."""
        if self.total_quotes == 0:
            return "no quote lines parsed"
        return (
            f"{self.verified_count}/{self.total_quotes} quotes verified "
            f"({self.unverified_count} unverified)"
        )


# Quote-line regex — tolerates:
#   - Optional spaces around the leading `>`.
#   - Em-dash or en-dash inside the timestamp group.
#   - Double-spaces between sender and the `(`.
#   - Any non-`)` chars inside the parens (timestamps can look like
#     "10:37 PM" / "today 14:02" / "2026-05-17 14:01" etc.).
# Rejects:
#   - Lines with no `(...)` group (those aren't well-formed quotes).
#   - Lines with no `:` after the closing paren.
_QUOTE_LINE_RE: re.Pattern[str] = re.compile(
    r"^[ \t]*>[ \t]*"     # blockquote marker (no newline-swallowing)
    r"([^()\n]+?)"        # sender (no parens)
    r"[ \t]*\("           # opening paren, with horizontal whitespace only
    r"([^)\n]+)"          # timestamp content (no closing paren, no newline)
    r"\)[ \t]*[:–—][ \t]*"  # closing paren, then `:` or en/em-dash separator
    r"(.+?)[ \t]*$",      # content, stripped trailing horizontal whitespace
    re.MULTILINE,
)


def _normalize_for_match(text: str) -> str:
    """Whitespace-collapse + lowercase for substring matching.

    Pure helper. Collapses every run of whitespace (spaces, tabs,
    newlines) to a single space, strips leading/trailing whitespace and
    common terminal punctuation, and lowercases. Matches both sides of
    the substring check so trailing-period drift doesn't break verify.
    """
    if not text:
        return ""
    collapsed = re.sub(r"\s+", " ", text).strip().lower()
    # Strip trailing terminal punctuation that often diverges between
    # the brain's paraphrase ("...to 6.") and the live tool output
    # ("...to 6 cities" or "...to 6!").
    return collapsed.rstrip(" .,!?;:")


def parse_quote_lines(reply_text: str) -> list[QuoteTuple]:
    """Extract every ``> sender (ts): content`` line from ``reply_text``.

    Pure function. Returns quotes in document order with 1-based line
    numbers. Lines that don't match the expected shape are silently
    skipped — the verifier only cares about well-formed quote claims.
    """
    if not reply_text:
        return []
    quotes: list[QuoteTuple] = []
    for m in _QUOTE_LINE_RE.finditer(reply_text):
        sender = m.group(1).strip()
        timestamp = m.group(2).strip()
        content = m.group(3).strip()
        if not content:
            continue
        # Count newlines before this match's start offset; +1 → 1-based.
        line_no = reply_text.count("\n", 0, m.start()) + 1
        quotes.append(QuoteTuple(
            sender=sender,
            timestamp=timestamp,
            content=content,
            line_no=line_no,
        ))
    return quotes


def verify_quotes_against_tool_results(
    quotes: Iterable[QuoteTuple],
    tool_results: Iterable[str],
) -> VerificationReport:
    """Check each quote's content appears in at least one tool result.

    Pure function. Uses whitespace-normalized, lowercase substring
    matching against the first ``_PROBE_PREFIX_CHARS`` of each quote's
    content. Quotes shorter than ``_MIN_PROBE_CHARS`` are skipped (still
    counted as verified — no factual claim worth checking).

    Returns a ``VerificationReport`` describing the outcome. Never raises.
    """
    quote_list = list(quotes)
    if not quote_list:
        return VerificationReport(
            total_quotes=0,
            verified_count=0,
            unverified_count=0,
            unverified_quotes=(),
        )

    normalized_results: list[str] = [
        _normalize_for_match(tr) for tr in tool_results if tr
    ]
    haystack: str = " ␟ ".join(normalized_results) if normalized_results else ""

    verified = 0
    unverified: list[tuple[QuoteTuple, str]] = []
    for q in quote_list:
        probe = _normalize_for_match(q.content)[:_PROBE_PREFIX_CHARS]
        if len(probe) < _MIN_PROBE_CHARS:
            # Too short to be a meaningful factual claim — don't flag.
            verified += 1
            continue
        if not haystack:
            unverified.append((q, "no tool results available this turn"))
            continue
        if probe in haystack:
            verified += 1
        else:
            unverified.append((q, "content not found in any tool result"))

    return VerificationReport(
        total_quotes=len(quote_list),
        verified_count=verified,
        unverified_count=len(unverified),
        unverified_quotes=tuple(unverified),
    )


def observe_or_enforce(
    reply_text: str,
    tool_results: Iterable[str],
) -> tuple[VerificationReport, bool]:
    """Wire-in helper for ``agent.py``.

    Runs ``parse_quote_lines`` + ``verify_quotes_against_tool_results`` and
    emits a WARNING log when unverified quotes are present. Returns
    ``(report, should_block)`` where ``should_block`` is always ``False``
    while ``F1_PHASE2_ENFORCE`` is ``False`` (observation-only). When the
    flag is flipped to ``True``, ``should_block`` will mirror
    ``report.unverified_count > 0`` so the caller can re-prompt the brain.

    Callers MUST treat ``should_block`` as advisory until enforcement is
    enabled. Never raises.
    """
    try:
        quotes = parse_quote_lines(reply_text or "")
        report = verify_quotes_against_tool_results(
            quotes, tool_results or [],
        )
    except Exception:  # noqa: BLE001 — verifier must never crash the loop
        logger.debug("F1 phase-2 verifier raised; ignoring", exc_info=True)
        empty = VerificationReport(0, 0, 0, ())
        return empty, False

    if report.unverified_count > 0:
        samples = "; ".join(
            f"line {q.line_no} ({q.sender}): {q.content[:60]!r} — {reason}"
            for q, reason in report.unverified_quotes[:2]
        )
        logger.warning(
            "[F1-phase2-obs] reply contains %d quotes; %d unverified "
            "against fresh tool results. Unverified samples: %s",
            report.total_quotes, report.unverified_count, samples,
        )

    should_block = F1_PHASE2_ENFORCE and report.unverified_count > 0
    return report, should_block
