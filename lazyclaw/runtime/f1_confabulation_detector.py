"""F1 confabulation detector — catches "tool-failure lie" and "made-up quote".

Background
==========
F1 phase-1 (``agent._check_f1_violation``) catches drafts that paraphrase
from memory instead of quoting the fresh channel-read tool result. Under
retry pressure, Opus 4.6/4.7 has been observed to ESCAPE the F1 loop by
inventing a tool failure (e.g. "No Upwork conversations found, make sure
you're logged in") — even though the tool returned real data. The 2026-05-20
14:23 incident logged the brain calling ``upwork_last_conversation`` →
real James Blue data returned → F1 rejected the draft twice for paraphrasing
→ third draft was a confabulated failure claim.

This module closes that escape hatch with TWO independent checks:

1. **Failure-claim confabulation** — reply asserts a tool failed/empty BUT
   ``_tool_results`` for this turn contains successful read-tool output.
2. **Made-up-quote confabulation** — reply contains ``> sender (ts): …``
   quote lines whose ``content`` doesn't appear in ``_tool_results``
   (reuses ``f1_content_verifier.verify_quotes_against_tool_results``).

On detection, the caller injects the raw tool output verbatim and re-rolls
the brain ONCE. If that final attempt still fails F1's structural check,
the caller logs ``[F1-accepted-degraded]`` and ships the reply anyway —
better a slightly imperfect quote than a confabulated lie.

The detector is PURE — it returns a verdict object and never mutates its
inputs. All log emission is left to the caller (so the agent's logger
name stays attached to the warning).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lazyclaw.runtime.f1_content_verifier import (
    parse_quote_lines,
    verify_quotes_against_tool_results,
)


# ─── failure-claim patterns ──────────────────────────────────────────────
# These are case-insensitive substring patterns that signal the brain is
# asserting a tool failed / returned empty / requires user action to log
# in. Each one is anchored loosely enough to catch the common variants
# Opus emits under retry pressure but tight enough to avoid matching
# legitimate user-facing copy ("no new messages today" — that's a
# legitimate empty-state, not a tool failure).
_FAILURE_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bno\s+\w+\s+(conversations?|messages?|threads?|results?|data)\s+found\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btool\s+returned\s+empty\b", re.IGNORECASE),
    re.compile(
        r"\b(could ?not|couldn't)\s+(fetch|retrieve|read|access|connect)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bsession\s+(has\s+)?expired\b", re.IGNORECASE),
    re.compile(
        r"\b(no|zero)\s+(matching|relevant)\s+(results?|threads?|messages?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bthe\s+(tool|read|fetch|call)\s+(failed|returned\s+(nothing|empty))\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bempty\s+(response|result)\b", re.IGNORECASE),
    re.compile(r"\bmake\s+sure\s+you('re|\s+are)\s+logged\s+in\b", re.IGNORECASE),
)


# ─── read-tool name match (mirrors agent._is_channel_read_tool) ──────────
# Kept local rather than imported to avoid a circular dep on agent.py.
# The suffix list matches what _CHANNEL_READ_TOOL_SUFFIXES covers plus
# the lazybrain read family (semantic_search / recall / list).
_READ_TOOL_SUFFIXES: frozenset[str] = frozenset({
    "_get_messages",
    "_get_conversation",
    "_get_unread_count",
    "_read",
    "_read_dms",
    "_read_feed",
    "_read_profile",
    "_list_chats",
    "_semantic_search",
    "_recall",
    "_list_notes",
})

_READ_TOOL_EXACT: frozenset[str] = frozenset({
    "upwork_last_conversation",
    "upwork_inbox_check",
    "upwork_contract_poll",
    "lazybrain_semantic_search",
    "lazybrain_recall_typed_memory",
    "list_memories",
    "recall_memories",
    "find_contact",
})

# Minimum non-whitespace bytes a tool result must contain to count as a
# "successful payload". Tiny outputs ("ok", "[]", "{}") shouldn't refute
# a "no data" failure claim — they're effectively empty.
_MIN_NONEMPTY_PAYLOAD_BYTES: int = 16


def _strip_mcp_prefix(name: str) -> str:
    """Strip the ``mcp_<uuid>_`` prefix from an MCP-bridged tool name.

    The same pattern lives in ``agent._is_channel_read_tool``; mirrored
    here to keep this module self-contained.
    """
    if not name or not name.startswith("mcp_"):
        return (name or "").lower()
    parts = name.lower().split("_", 2)
    if len(parts) == 3:
        return parts[2]
    return name.lower()


def _is_read_tool(name: str) -> bool:
    """True if ``name`` is a read tool whose result is authoritative."""
    if not name:
        return False
    base = _strip_mcp_prefix(name)
    if base in _READ_TOOL_EXACT:
        return True
    return any(base.endswith(suf) for suf in _READ_TOOL_SUFFIXES)


def _has_successful_read_payload(
    tool_call_history: list[str],
    tool_results: list[str],
) -> tuple[bool, str, int]:
    """Scan this turn's tool calls + results for a successful read payload.

    Returns ``(found, tool_name, payload_bytes)`` where ``found`` is True
    iff a read tool was called AND its result is at least
    ``_MIN_NONEMPTY_PAYLOAD_BYTES`` of non-whitespace content. Pairs the
    last read-tool call with the last result (matches the agent loop's
    append-in-lockstep contract on lines ~4700 of agent.py).

    Pure helper. Never raises. Returns ``(False, "", 0)`` on any mismatch
    (e.g. histories of different lengths, both empty).
    """
    if not tool_call_history or not tool_results:
        return False, "", 0
    # Walk newest→oldest so the most-recent successful read wins.
    n = min(len(tool_call_history), len(tool_results))
    for i in range(n - 1, -1, -1):
        name = tool_call_history[i]
        result = tool_results[i] or ""
        if not _is_read_tool(name):
            continue
        # Compact the result the same way a human would: strip whitespace,
        # then measure. JSON braces/brackets count as content.
        compact = "".join(result.split())
        if len(compact) >= _MIN_NONEMPTY_PAYLOAD_BYTES:
            return True, name, len(result)
    return False, "", 0


def _find_failure_claim(reply: str) -> str | None:
    """Return the first failure-claim phrase found in ``reply``, or None."""
    if not reply:
        return None
    for pat in _FAILURE_CLAIM_PATTERNS:
        m = pat.search(reply)
        if m:
            return m.group(0)
    return None


# Wikilink-in-quote: hard memory-leak signal. Real humans on Upwork /
# WhatsApp / Telegram never send `[[wikilink]]` syntax — that's lazybrain
# / Obsidian notation. If it appears inside ANY of the brain's three
# contact-quote formats, the brain copy-pasted content from a lazybrain
# memory note and presented it as the live conversation. The 2026-05-21
# 22:13 incident: brain quoted `Mac [[computer]] iPad` for James Blue —
# the [[computer]] proves the content came from
# `reference_james_blue_contract.md`, not Upwork.
#
# The brain has been observed emitting quotes in THREE shapes:
#   1. Markdown blockquote:  `> James (9:12 PM): I have a Mac [[X]]`
#   2. Bold-sender:          `**James (9:12 PM):** I have a Mac [[X]]`
#   3. Plain sender prefix:  `James Blue (9:12 PM): I have a Mac [[X]]`
# Yesterday's detector only caught (1). 2026-05-22 10:09 incident leaked
# `[[computer]]` through (2). The detector below catches all three.
_WIKILINK_IN_TEXT_RE: re.Pattern[str] = re.compile(r"\[\[([^\]\n]+)\]\]")

# Bold-sender shape: `**Sender Name (HH:MM[ AM/PM]?):**` at line start.
# Captures the content portion after the closing `**:**` for wikilink scan.
_BOLD_SENDER_LINE_RE: re.Pattern[str] = re.compile(
    r"^\*\*[^*\n]{1,80}\([^)\n]{1,40}\)\s*:\*\*\s*(?P<content>.*)$",
)

# Plain-sender shape: `Sender Name (HH:MM[ AM/PM]?):` at line start. The
# sender token must start with a capital letter and look name-shaped — this
# is intentionally tight so we don't flag headers like `Note (2026-05-22):`.
_PLAIN_SENDER_LINE_RE: re.Pattern[str] = re.compile(
    r"^[A-Z][\w \-\.']{0,60}\s*\([^)\n]{1,40}\)\s*:\s*(?P<content>.*)$",
)


def _find_wikilink_in_quote_block(reply: str) -> str | None:
    """Scan ``reply`` for wikilink syntax inside any contact-quote line.

    Recognises THREE quote formats:
      1. Markdown blockquote — line starts with ``>`` (every continuation
         line of a multi-paragraph quote also starts with ``>``, so the
         per-line scan catches them all).
      2. Bold-sender         — ``**Sender (timestamp):**`` line prefix.
         The brain often emits the body on subsequent NAKED continuation
         lines (no ``>``, no ``**``). The detector tracks
         "inside-bold-quote" state and scans continuation lines until a
         blank line, a ``---`` horizontal rule, or the next sender line.
      3. Plain-sender        — ``Sender Name (timestamp):`` line prefix
         with the same continuation-line treatment as shape 2.

    Returns the first wikilink token found (e.g. ``"[[computer]]"``) or
    None. Wikilinks in headers / free prose / inline markdown links
    (``[text](url)``) are NOT flagged — only quote lines and their
    continuations.

    Multi-line continuation bug fixed 2026-05-23: previously the bold
    and plain shapes only scanned the sender-prefix line itself. When
    the brain split a long quote across lines like

        **James (9:12 PM):** Which platform...
        Mac [[computer]] Once I have these...

    the second line matched no sender pattern and the wikilink was
    missed. Today's incident: ``Mac [[computer]]`` survived the
    detector because of this gap. See ``project_self_perpetuating_
    hallucination`` memory note.
    """
    if not reply:
        return None

    # Track "inside an open bold/plain quote" state across lines so we
    # can scan continuation lines for wikilinks even when they don't
    # start with a sender prefix.
    in_open_quote: bool = False

    for raw_line in reply.splitlines():
        stripped = raw_line.lstrip()

        # Blank line or horizontal rule → end the open quote span.
        if not stripped or stripped.startswith("---"):
            in_open_quote = False
            continue

        # Shape 1: Markdown blockquote. Each `>` line is its own quote
        # body — no continuation-state needed; the per-line scan handles
        # multi-paragraph quotes naturally.
        if stripped.startswith(">"):
            in_open_quote = False  # `>` lines are self-contained
            m = _WIKILINK_IN_TEXT_RE.search(stripped)
            if m:
                return m.group(0)
            continue

        # Shape 2: Bold-sender prefix opens a quote span.
        bold_match = _BOLD_SENDER_LINE_RE.match(stripped)
        if bold_match:
            content = bold_match.group("content")
            wl = _WIKILINK_IN_TEXT_RE.search(content)
            if wl:
                return wl.group(0)
            in_open_quote = True
            continue

        # Shape 3: Plain-sender prefix opens a quote span.
        plain_match = _PLAIN_SENDER_LINE_RE.match(stripped)
        if plain_match:
            content = plain_match.group("content")
            wl = _WIKILINK_IN_TEXT_RE.search(content)
            if wl:
                return wl.group(0)
            in_open_quote = True
            continue

        # Continuation line of an open bold/plain quote — scan for
        # wikilinks. This is the fix for today's bug.
        if in_open_quote:
            wl = _WIKILINK_IN_TEXT_RE.search(stripped)
            if wl:
                return wl.group(0)

    return None


@dataclass(frozen=True)
class ConfabulationVerdict:
    """Outcome of running the detector on one draft.

    Immutable so callers can log/forward freely. The two failure modes
    are tracked separately so the caller can craft a precise corrective
    system message:

    Fields:
        is_confabulation: True iff EITHER failure mode tripped.
        kind: ``"failure_claim"`` | ``"made_up_quote"`` | ``""`` (clean).
        offending_phrase: The matched failure-claim substring, or "".
        unverified_quote_count: How many quote lines didn't match
            tool_results (only populated for kind="made_up_quote").
        tool_name: Name of the read tool whose payload refutes the claim
            (for ``failure_claim``) or the read tool whose data the
            invented quotes contradict (for ``made_up_quote``).
        payload_bytes: Size of that tool's result (for log context).
    """

    is_confabulation: bool
    kind: str  # "" | "failure_claim" | "made_up_quote" | "wikilink_in_quote"
    offending_phrase: str
    unverified_quote_count: int
    tool_name: str
    payload_bytes: int


_CLEAN_VERDICT: ConfabulationVerdict = ConfabulationVerdict(
    is_confabulation=False,
    kind="",
    offending_phrase="",
    unverified_quote_count=0,
    tool_name="",
    payload_bytes=0,
)


def detect_confabulation(
    reply: str,
    tool_call_history: list[str],
    tool_results: list[str],
) -> ConfabulationVerdict:
    """Run both confabulation checks against ``reply``.

    Pure function. Never raises (defensive try/except wraps each
    sub-check). Returns ``_CLEAN_VERDICT`` if the reply is grounded.

    Order matters: ``failure_claim`` is checked first because it's the
    cheaper check AND the more dangerous failure mode (the brain lying
    to the user that the tool failed). If a failure claim is found AND
    a successful read payload exists this turn, that beats any quote
    check — the reply needs to be torn up and re-rolled from scratch.
    """
    if not reply:
        return _CLEAN_VERDICT

    # ── Check 0: wikilink inside a quote block (hardest signal) ──
    # No real contact sends [[wikilink]] syntax — if it appears inside a
    # `> sender (ts): ...` line OR on a continuation line of an open
    # bold/plain-sender quote, the brain copied the content from a
    # lazybrain memory note. Check this FIRST because it's deterministic
    # and the most damning evidence of memory-leak hallucination.
    #
    # Wikilinks in headers, free prose, or paragraphs separated from a
    # quote by a blank line are LEGITIMATE references (e.g. "I'll save
    # this to [[contact_James]]") and must NOT trip the detector. The
    # state machine in `_find_wikilink_in_quote_block` enforces that.
    #
    # Continuation-line state machine added 2026-05-23 after the bold
    # multi-line shape leaked `Mac [[computer]]` past the detector.
    try:
        wl = _find_wikilink_in_quote_block(reply)
        if wl:
            last_read = ""
            last_bytes = 0
            for i in range(len(tool_call_history) - 1, -1, -1):
                if _is_read_tool(tool_call_history[i]):
                    last_read = tool_call_history[i]
                    if i < len(tool_results):
                        last_bytes = len(tool_results[i] or "")
                    break
            return ConfabulationVerdict(
                is_confabulation=True,
                kind="wikilink_in_quote",
                offending_phrase=wl,
                unverified_quote_count=0,
                tool_name=last_read,
                payload_bytes=last_bytes,
            )
    except Exception:
        pass

    # ── Check 1: failure-claim against successful read payload ──
    try:
        phrase = _find_failure_claim(reply)
        if phrase:
            has_payload, tool_name, payload_bytes = _has_successful_read_payload(
                tool_call_history, tool_results,
            )
            if has_payload:
                return ConfabulationVerdict(
                    is_confabulation=True,
                    kind="failure_claim",
                    offending_phrase=phrase,
                    unverified_quote_count=0,
                    tool_name=tool_name,
                    payload_bytes=payload_bytes,
                )
    except Exception:
        # Defensive: a malformed pattern or histories shouldn't crash the
        # turn. Fall through to the next check.
        pass

    # ── Check 2: made-up quotes (inverse confabulation) ──
    try:
        # Only meaningful when at least one read tool was called this turn.
        # If no tool ran, any quotes are obviously not "matching" but
        # that's a different problem (handled by F1 phase-1 + prompt rules).
        if any(_is_read_tool(n) for n in tool_call_history):
            quotes = parse_quote_lines(reply)
            if quotes:
                report = verify_quotes_against_tool_results(
                    quotes, tool_results,
                )
                # Require BOTH (a) at least one unverified quote AND
                # (b) the unverified count to be a majority — single
                # near-miss quotes can be normalization drift, but if
                # most quotes don't match, the brain invented them.
                if (
                    report.unverified_count > 0
                    and report.unverified_count >= max(1, report.total_quotes // 2)
                ):
                    # Identify the read tool whose data the quotes
                    # purport to come from — use the most recent read.
                    last_read = ""
                    last_bytes = 0
                    for i in range(len(tool_call_history) - 1, -1, -1):
                        if _is_read_tool(tool_call_history[i]):
                            last_read = tool_call_history[i]
                            if i < len(tool_results):
                                last_bytes = len(tool_results[i] or "")
                            break
                    return ConfabulationVerdict(
                        is_confabulation=True,
                        kind="made_up_quote",
                        offending_phrase="",
                        unverified_quote_count=report.unverified_count,
                        tool_name=last_read,
                        payload_bytes=last_bytes,
                    )
    except Exception:
        pass

    return _CLEAN_VERDICT


# ─── raw-data injection helpers ──────────────────────────────────────────

# Max chars of raw tool output to inject into the corrective prompt.
# This payload is ALWAYS a read-tool result (see _select_raw_payload's
# _is_read_tool gate), so the F1 repair must re-inject the *whole*
# conversation — a 4 KB cap here silently re-introduced the exact
# 2026-05-24 truncation incident that the 50 KB channel-read result cap
# (agent._MAX_TOOL_RESULT_CHARS_CHANNEL_READ) was added to fix: a 10.9 KB
# Upwork conversation JSON got chopped to ~4 KB, the F1 retry only saw the
# first 2-3 messages, and the brain re-confabulated "no new messages".
# Matched to the channel-read cap (50000) so the repair sees what the
# original read saw.
_RAW_INJECTION_TRUNCATE_CHARS: int = 50000

_TRUNCATE_SUFFIX: str = "\n...[truncated]"


def _select_raw_payload(
    tool_call_history: list[str],
    tool_results: list[str],
) -> tuple[str, str]:
    """Pick the most-recent successful read result to inject.

    Returns ``(tool_name, payload)`` where ``payload`` is truncated to
    ``_RAW_INJECTION_TRUNCATE_CHARS`` with a marker suffix when over the
    limit. Returns ``("", "")`` when no eligible payload exists.
    """
    if not tool_call_history or not tool_results:
        return "", ""
    n = min(len(tool_call_history), len(tool_results))
    for i in range(n - 1, -1, -1):
        name = tool_call_history[i]
        result = tool_results[i] or ""
        if not _is_read_tool(name):
            continue
        if len("".join(result.split())) < _MIN_NONEMPTY_PAYLOAD_BYTES:
            continue
        if len(result) > _RAW_INJECTION_TRUNCATE_CHARS:
            payload = result[:_RAW_INJECTION_TRUNCATE_CHARS] + _TRUNCATE_SUFFIX
        else:
            payload = result
        return name, payload
    return "", ""


def build_raw_data_injection(
    verdict: ConfabulationVerdict,
    tool_call_history: list[str],
    tool_results: list[str],
    *,
    reason: str = "confabulation",
) -> str:
    """Build the corrective system message that spoon-feeds raw tool data.

    Used in TWO situations:
    1. Confabulation detected (verdict.is_confabulation is True).
    2. F1 retries exhausted (caller passes a synthetic clean verdict).

    The message includes the raw tool output verbatim + a directive to
    use quote-then-summarize formatting. Pure function — never raises.

    ``reason`` is one of ``"confabulation"`` or ``"retry_exhausted"``.
    Controls only the wording of the opening line.
    """
    tool_name = verdict.tool_name
    payload = ""
    if tool_call_history and tool_results:
        picked_name, picked_payload = _select_raw_payload(
            tool_call_history, tool_results,
        )
        if picked_payload:
            tool_name = picked_name or tool_name
            payload = picked_payload

    if reason == "confabulation" and verdict.kind == "failure_claim":
        opening = (
            f"MANDATORY REWRITE — CONFABULATION DETECTED. The tool "
            f"{tool_name or '(unknown)'} returned successful data this turn "
            "(see RAW DATA below). You wrote that the tool failed. That was "
            "false."
        )
    elif reason == "confabulation" and verdict.kind == "made_up_quote":
        opening = (
            f"MANDATORY REWRITE — CONFABULATION DETECTED. The tool "
            f"{tool_name or '(unknown)'} returned data this turn (see RAW "
            "DATA below). Your previous reply contained quote lines whose "
            f"content does NOT appear in that data "
            f"({verdict.unverified_quote_count} unverified quotes)."
        )
    elif reason == "confabulation" and verdict.kind == "wikilink_in_quote":
        opening = (
            f"MANDATORY REWRITE — MEMORY LEAK DETECTED. Your previous reply "
            f"contained the wikilink `{verdict.offending_phrase}` inside a "
            "quote block. Real contacts never send wikilink syntax — this "
            "proves the content was copied from a lazybrain memory note, "
            "not from the live tool result. The RAW DATA below is the "
            "ONLY source you may quote from."
        )
    else:
        # retry_exhausted path
        opening = (
            "MANDATORY REWRITE — F1 retries exhausted. Use the raw data "
            "below as the ONLY source of truth."
        )

    directive = (
        "\n\nRules — non-negotiable:\n"
        "1. Every line inside a `> sender (ts): ...` quote block MUST appear "
        "character-for-character in the RAW DATA below. If it doesn't, do "
        "not write it.\n"
        "2. Wikilink syntax `[[X]]` is FORBIDDEN in quote blocks. Wikilinks "
        "come from your memory store, not from real contacts.\n"
        "3. If the RAW DATA lacks what the user asked about, say so "
        "explicitly: \"The tool returned N messages but the specific info "
        "you asked about is not present.\" Do NOT fill gaps from prior "
        "conversation or memory.\n"
        "4. Do not invent timestamps or sender names. If a bubble has no "
        "timestamp, write `(no timestamp)`.\n"
        "5. Do NOT claim the tool failed.\n\n"
        "Format the 3 most recent contact-side entries as "
        "`> {sender} ({timestamp if available}): {verbatim content}`, then "
        "summarize using only quoted content."
    )

    if not payload:
        # Shouldn't happen in the confabulation path (the verdict requires
        # a successful payload to fire), but defensive default.
        return f"[SYSTEM: {opening}{directive}]"

    return (
        f"[SYSTEM: {opening}{directive}\n\n"
        f"--- RAW DATA from {tool_name or 'tool'} ---\n"
        f"{payload}\n"
        f"--- END RAW DATA ---]"
    )
