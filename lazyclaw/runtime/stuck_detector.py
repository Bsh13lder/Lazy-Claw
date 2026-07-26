"""Stuck detection for agent loops — pure functions, no side effects.

Detects when the agent is stuck in a loop, hitting CAPTCHAs,
or getting repeated errors. Returns structured StuckSignal for
the agent loop to act on.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StuckSignal:
    """Immutable signal indicating the agent is stuck."""

    reason: str          # "loop", "captcha", "repeated_error"
    tool_name: str       # which tool triggered the stuck state
    context: str         # human-readable description for the user
    needs_browser: bool  # whether visible browser handoff is relevant
    url: str | None = None


@dataclass(frozen=True)
class PivotSignal:
    """The brain has been iterating same-shape tool calls inline instead
    of dispatching workers.

    Architectural rule: brain dispatches, workers execute. When N
    consecutive tool calls share the same fingerprint
    ``(tool_name, sorted(arg_keys))``, the brain has slipped into worker
    role for what should be a fan-out (``dispatch_subagents``) or a
    batch background (``run_background``). Caller injects a system
    message redirecting it.
    """

    reason: str
    tool_name: str
    arg_keys: tuple[str, ...]
    count: int


# Tools that legitimately repeat without indicating worker-loop drift —
# the brain pulling memory and saving facts is part of its dispatcher
# role, not a per-item loop. Excluded from the pivot fingerprint count.
_PIVOT_EXEMPT_TOOLS: frozenset[str] = frozenset({
    "recall_memories", "save_memory", "search_tools",
})


def detect_inline_pivot(
    tool_call_signatures: list[tuple[str, tuple[str, ...]]],
    *,
    threshold: int = 5,
) -> PivotSignal | None:
    """N same-shape tool calls in a row → brain is acting as a worker.

    ``tool_call_signatures`` is the per-turn ordered list of
    ``(tool_name, sorted_arg_keys)`` tuples. The function looks at the
    trailing run: if the last K entries share a single fingerprint and
    K >= threshold, it returns a PivotSignal. Memory / discovery tools
    are filtered out by the caller (see ``_PIVOT_EXEMPT_TOOLS``).

    Pure function — no side effects, easy to unit-test.
    """
    if threshold < 2 or not tool_call_signatures:
        return None

    # Walk backwards counting how many trailing entries match the latest.
    last = tool_call_signatures[-1]
    if last[0] in _PIVOT_EXEMPT_TOOLS:
        return None
    count = 0
    for sig in reversed(tool_call_signatures):
        if sig != last:
            break
        count += 1
    if count < threshold:
        return None
    logger.debug(
        "[stuck] inline-pivot trip tool=%s consecutive=%d threshold=%d outcome=blocked",
        last[0], count, threshold,
    )
    return PivotSignal(
        reason="same_shape_inline_loop",
        tool_name=last[0],
        arg_keys=last[1],
        count=count,
    )


# ── CAPTCHA detection ────────────────────────────────────────────────

_CAPTCHA_RE = re.compile(
    r"(recaptcha|hcaptcha|rc-anchor|g-recaptcha|cf-turnstile|"
    r"captcha_image|captcha-solver|verify you are human|"
    r"verify you're human|prove you're not a robot|"
    r"complete the security check|cloudflare challenge)",
    re.IGNORECASE,
)


def detect_captcha(tool_result: str) -> StuckSignal | None:
    """Check if a tool result indicates a CAPTCHA challenge.

    Returns StuckSignal if CAPTCHA detected, None otherwise.
    """
    if not tool_result:
        return None

    match = _CAPTCHA_RE.search(tool_result)
    if match:
        logger.debug("[stuck] captcha trip tool=browser outcome=blocked")
        return StuckSignal(
            reason="captcha",
            tool_name="browser",
            context=f"CAPTCHA detected ({match.group()}). This needs a human to solve.",
            needs_browser=True,
        )
    return None


# ── Tool loop detection ──────────────────────────────────────────────

# Default limits: how many consecutive calls before stuck
# Note: same-result detector catches the real stuck case (identical results).
# Tool loop detector only catches mindless repetition of the same tool.
DEFAULT_LOOP_LIMITS: dict[str, int] = {
    "browser": 5,  # Browser workflows need steps — cap at 5 (was 8, but graduated recovery kicks in sooner)
    "web_search": 6,  # Research needs 3-5 searches then synthesis
    "list_directory": 3,  # Directory listing should not loop
    # Natural sequence tools — shell debugging / file reads often chain
    # many identical-name calls with different args (`cd`, `ls`, `cat`,
    # `git status`...). The same-result detector still catches true loops.
    "run_command": 8,
    "read_file": 8,
    "write_file": 6,
    # Lookup / memory tools — the brain pings these trying to "find the
    # answer" when the answer doesn't exist there. Each call returns
    # different content (so the similarity-bypass below would let it
    # loop forever), but the BRAIN isn't getting unstuck. Hard caps so
    # the runtime intervenes after a sensible number of pings.
    # Today's 2026-05-13 18:16 turn looped recall_memories 13× before
    # MAX_ITERATIONS killed it. 4 is enough for "two topics + a couple
    # of refinements" and not enough to grind a turn.
    "recall_memories": 4,
    "search_tools": 4,
    "default": 3,
}

# Tools that should hit their loop cap EVEN WHEN results vary. Normal
# tools (browser, email_get) reading different IDs return different
# content — that's batch progress, not a loop, and the similarity check
# correctly suppresses. Lookup/memory tools always return different
# content (the brain varies the query string), but the brain is still
# stuck if it pings them N times without pivoting to action.
_LOOKUP_TOOLS_BYPASS_SIMILARITY: frozenset[str] = frozenset({
    "recall_memories", "search_tools",
})

# MCP tools that do batch operations (email organize, bulk label, etc.)
# These need higher limits because one "organize inbox" task = many calls.
# The same-result detector still catches true stuck loops.
# `lazybrain_` belongs here too: search_notes → get_note(id_a) → get_note(id_b)
# is the normal read pattern, and each call has different args + different results.
_BATCH_OP_PREFIXES = (
    "email_", "whatsapp_", "instagram_", "lazybrain_", "upwork_",
)

# Bridged MCP tools are registered as ``mcp_<server_uuid>_<tool>``
# (lazyclaw/mcp/bridge.py builds the name from the server's DB id, not
# its display name). 2026-07-26: that meant NO entry in
# DEFAULT_LOOP_LIMITS and NO prefix above could ever match a bridged
# tool — every one silently fell through to ``"default": 3`` under a
# name no rule could address. Anchored on the 8-4-4-4-12 hex UUID shape
# because a naive ``split("_")`` mangles both the hyphenated UUID and
# the underscore-bearing tool name.
_MCP_UUID_PREFIX_RE = re.compile(
    r"^mcp_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_",
)


def strip_mcp_prefix(tool_name: str) -> str:
    """``mcp_<uuid>_upwork_get_messages`` → ``upwork_get_messages``.

    Returns the name unchanged when it doesn't match the bridged shape —
    never mangle a name we don't recognise.
    """
    return _MCP_UUID_PREFIX_RE.sub("", tool_name, count=1)


def _effective_limit(tool_name: str, limits: dict[str, int]) -> int:
    """Get loop limit for a tool, with higher defaults for batch-op tools."""
    if tool_name in limits:
        return limits[tool_name]
    # Bridged MCP tools resolve under their BARE name so the table and
    # the batch-op prefixes below can actually see them.
    bare = strip_mcp_prefix(tool_name)
    if bare != tool_name and bare in limits:
        return limits[bare]
    # MCP batch tools get 10 consecutive calls before stuck
    for prefix in _BATCH_OP_PREFIXES:
        if bare.startswith(prefix):
            return 10
    return limits.get("default", 3)


def detect_tool_loop(
    history: list[str],
    limits: dict[str, int] | None = None,
    results: list[str] | None = None,
) -> StuckSignal | None:
    """Detect when the same tool is called N+ times consecutively.

    Browser gets a higher limit (multi-step navigation is normal).
    MCP batch tools (email_*, whatsapp_*) get 10 before stuck.

    If ``results`` is supplied, also requires the recent results to be
    ≥85% similar pairwise — different results means real progress
    (e.g. ``email_get(id=X)`` 24× with 24 distinct emails) and the
    detector should NOT fire just because the tool name repeats.
    """
    if not history:
        return None

    effective_limits = {**DEFAULT_LOOP_LIMITS, **(limits or {})}
    last_tool = history[-1]
    limit = _effective_limit(last_tool, effective_limits)

    if len(history) < limit:
        return None

    last_n = history[-limit:]
    if len(set(last_n)) != 1:
        return None

    # Same tool name N times — but if results are varying substantially,
    # this is batch progress (read 24 emails, fetch 30 notes, etc.),
    # not a stuck loop. Bypass the signal in that case.
    # EXCEPTION: lookup tools (recall_memories, search_tools) loop with
    # varying queries returning varying-but-useless content — they should
    # hit their cap regardless of result variety. See
    # _LOOKUP_TOOLS_BYPASS_SIMILARITY.
    if (
        last_tool not in _LOOKUP_TOOLS_BYPASS_SIMILARITY
        and results is not None and len(results) >= limit
    ):
        last_n_results = results[-limit:]
        for i in range(1, len(last_n_results)):
            if _similarity_ratio(last_n_results[i - 1], last_n_results[i]) < 0.85:
                logger.debug(
                    "[stuck] tool_loop trip=SUPPRESSED tool=%s consecutive=%d "
                    "limit=%d outcome=allowed (results diverged — batch progress)",
                    last_tool, limit, limit,
                )
                return None

    logger.debug(
        "[stuck] tool_loop trip tool=%s consecutive=%d limit=%d outcome=blocked",
        last_tool, limit, limit,
    )
    return StuckSignal(
        reason="loop",
        tool_name=last_tool,
        context=f"Called '{last_tool}' {limit} times in a row without progress.",
        needs_browser=(last_tool == "browser"),
    )


# ── Repeated error detection ────────────────────────────────────────

_ERROR_PREFIX = "Error"
_MIN_ERROR_STREAK = 2


def detect_repeated_errors(
    history: list[str],
    results: list[str],
) -> StuckSignal | None:
    """Detect when the same tool returns errors repeatedly.

    Checks if the last N tool results for the same tool all start
    with 'Error'. Requires at least 2 consecutive errors.
    """
    if len(history) < _MIN_ERROR_STREAK or len(results) < _MIN_ERROR_STREAK:
        return None

    last_tool = history[-1]

    # Collect last results for this tool
    error_count = 0
    for i in range(len(history) - 1, -1, -1):
        if i >= len(results):
            break  # Lists mismatched — treat streak as broken
        if history[i] != last_tool:
            break
        if results[i].startswith(_ERROR_PREFIX):
            error_count += 1
        else:
            break

    if error_count >= _MIN_ERROR_STREAK:
        logger.debug(
            "[stuck] repeated_error trip tool=%s consecutive=%d limit=%d outcome=blocked",
            last_tool, error_count, _MIN_ERROR_STREAK,
        )
        return StuckSignal(
            reason="repeated_error",
            tool_name=last_tool,
            context=f"'{last_tool}' failed {error_count} times in a row.",
            needs_browser=(last_tool == "browser"),
        )
    return None


# ── Intent-group detection ─────────────────────────────────────────
#
# The tool_name counter resets when the agent pivots from `n8n_update_workflow`
# to `run_command` (or `browser`) even though it's the SAME failing task —
# the model is just using a generic escape hatch to flail around the real
# problem. Group those together so the error streak keeps counting.
#
# "Intent group" = the set of skill name prefixes that belong to the same
# target system. When a call in a group fails AND the next call is either
# another call in the same group OR a generic fallback tool, count it as
# part of the same failure streak.

_INTENT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("n8n_",),
    ("email_",),
    ("whatsapp_",),
    ("instagram_",),
    ("gmail_",),
    ("google_",),
)

# Generic fallback tools the model reaches for when a domain-specific
# skill fails. If a recent call in an intent group failed, these should
# count toward the same streak.
_GENERIC_FALLBACK_TOOLS: frozenset[str] = frozenset({
    "run_command",
    "browser",
    "list_directory",
    "read_file",
    "write_file",
    "web_search",
})

_MIN_INTENT_ERROR_STREAK = 3


def _match_intent_group(tool_name: str) -> tuple[str, ...] | None:
    for group in _INTENT_GROUPS:
        if any(tool_name.startswith(p) for p in group):
            return group
    return None


def detect_intent_flail(
    history: list[str],
    results: list[str],
) -> StuckSignal | None:
    """Catch pivots-after-failure where the agent switches tools to dodge a wall.

    Example sequence that triggers this:
      n8n_update_workflow → Error
      n8n_update_workflow → Error      (counted by repeated_error already)
      run_command         → (any)      ← previously reset the streak
      run_command         → (any)      ← still counts toward same intent

    If the last N calls contain ≥2 errors in one intent group AND the
    most recent calls are generic fallbacks or more group failures,
    flag it as a stuck flail.
    """
    if len(history) < _MIN_INTENT_ERROR_STREAK or len(results) < _MIN_INTENT_ERROR_STREAK:
        return None

    window = min(len(history), 6)
    recent_tools = history[-window:]
    recent_results = results[-window:] if len(results) >= window else results[:]

    # Find a failing intent group in the window.
    failing_group: tuple[str, ...] | None = None
    group_error_count = 0
    for tool, res in zip(recent_tools, recent_results):
        group = _match_intent_group(tool)
        if group is None:
            continue
        if res.startswith(_ERROR_PREFIX):
            if failing_group is None or failing_group == group:
                failing_group = group
                group_error_count += 1

    if failing_group is None or group_error_count < 2:
        return None

    # The last tool must be either another call in the same group, or
    # one of the generic fallback tools — that's the "flail" signal.
    last_tool = recent_tools[-1]
    same_group = _match_intent_group(last_tool) == failing_group
    is_fallback = last_tool in _GENERIC_FALLBACK_TOOLS
    if not (same_group or is_fallback):
        return None

    # Count fallback calls since the last same-group failure — if there
    # are ≥2, it's the flail pattern we want to catch.
    fallback_since_fail = 0
    for tool in reversed(recent_tools):
        group = _match_intent_group(tool)
        if group == failing_group:
            break
        if tool in _GENERIC_FALLBACK_TOOLS:
            fallback_since_fail += 1

    if not same_group and fallback_since_fail < 2:
        return None

    prefix = failing_group[0].rstrip("_")
    logger.debug(
        "[stuck] intent_flail trip group=%s tool=%s group_errors=%d outcome=blocked",
        prefix, last_tool, group_error_count,
    )
    return StuckSignal(
        reason="intent_flail",
        tool_name=last_tool,
        context=(
            f"'{prefix}_*' failed {group_error_count}× in this turn — "
            f"agent pivoted to '{last_tool}' which is not a real fix. "
            "Stop and tell the user what's broken."
        ),
        needs_browser=False,
    )


# ── Same-result detection ──────────────────────────────────────────

_SAME_RESULT_THRESHOLD = 3


def _similarity_ratio(a: str, b: str) -> float:
    """Fast similarity check: length ratio + prefix overlap.

    Good enough to catch near-identical tool results (e.g. 6398 vs 6402 chars
    with the same page content) without pulling in difflib.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Length ratio — very different lengths means different content
    len_ratio = min(len(a), len(b)) / max(len(a), len(b))
    if len_ratio < 0.85:
        return len_ratio
    # Compare first 500 chars + last 500 chars (handles different headers, same body)
    prefix_match = sum(1 for x, y in zip(a[:500], b[:500]) if x == y) / min(500, min(len(a), len(b)))
    suffix_match = sum(1 for x, y in zip(a[-500:], b[-500:]) if x == y) / min(500, min(len(a), len(b)))
    return (len_ratio + prefix_match + suffix_match) / 3


def detect_same_result(
    results: list[str],
    threshold: int = _SAME_RESULT_THRESHOLD,
) -> StuckSignal | None:
    """Detect when the last N tool results are identical or >90% similar.

    Catches the case where the specialist calls browser.open() with different
    URLs but gets the same page content every time (e.g. SPA not navigating,
    or "No messages matched" repeated).
    """
    if len(results) < threshold:
        return None

    last_n = results[-threshold:]

    # Check if all results are similar to the first
    base = last_n[0]
    for other in last_n[1:]:
        if _similarity_ratio(base, other) < 0.90:
            return None

    # All similar — agent is stuck
    preview = base[:80].replace("\n", " ")
    logger.debug(
        "[stuck] same_result trip result_len=%d threshold=%d outcome=blocked",
        len(base), threshold,
    )
    return StuckSignal(
        reason="same_result",
        tool_name="unknown",
        context=(
            f"Last {threshold} tool calls returned nearly identical results "
            f"({len(base)} chars each). The page isn't changing. "
            f"Preview: \"{preview}...\""
        ),
        needs_browser=True,
    )


# ── No-progress detection (uses action verifier output) ───────────────

# The action_verifier appends "→ FAILED:" or "→ SUCCESS:" to browser results.
# If the last 2 browser results both contain a failure signal, the agent isn't
# making progress — trigger stuck before reaching the loop limit.
_VERIFIER_FAILED_MARKER = "→ FAILED:"
_VERIFIER_SUCCESS_MARKER = "→ SUCCESS:"
# 2 was too eager — the verifier is heuristic and a single unlucky
# screen read could flip it to FAILED while the agent was still
# making real progress. 3 keeps the detector in play for genuinely
# stuck flows while letting normal multi-step navigation breathe.
_NO_PROGRESS_THRESHOLD = 3


def detect_no_progress(
    tool_history: list[str],
    tool_results: list[str],
) -> StuckSignal | None:
    """Detect when the last N browser actions all returned FAILED verification.

    Requires at least _NO_PROGRESS_THRESHOLD browser results with the
    verifier's FAILED marker. Triggers stuck earlier than the loop limit.
    """
    if len(tool_history) < _NO_PROGRESS_THRESHOLD or len(tool_results) < _NO_PROGRESS_THRESHOLD:
        return None

    # Collect the most recent browser results (in order)
    failed_streak = 0
    for i in range(len(tool_history) - 1, -1, -1):
        if i >= len(tool_results):
            break
        if tool_history[i] != "browser":
            break  # Non-browser tool interrupts the streak
        result = tool_results[i]
        if _VERIFIER_FAILED_MARKER in result:
            failed_streak += 1
        elif _VERIFIER_SUCCESS_MARKER in result:
            break  # A success interrupts the failure streak
        # No verifier output (old snapshot/read actions) — don't count

    if failed_streak >= _NO_PROGRESS_THRESHOLD:
        logger.debug(
            "[stuck] no_progress trip tool=browser consecutive=%d limit=%d outcome=blocked",
            failed_streak, _NO_PROGRESS_THRESHOLD,
        )
        return StuckSignal(
            reason="no_progress",
            tool_name="browser",
            context=(
                f"Last {failed_streak} browser actions failed verification. "
                f"The page isn't responding as expected."
            ),
            needs_browser=True,
        )
    return None


# ── Hallucinated element detection ───────────────────────────────────

# Phrases in browser results indicating the agent tried to click/type a ref
# that doesn't exist. Repeating this is a hallucination loop.
_HALLUCINATED_REF_PHRASES = (
    "ref not found", "element not found", "no element with ref",
    "invalid ref", "ref does not exist", "unknown ref",
    "not in snapshot", "ref not in",
)
_HALLUCINATION_THRESHOLD = 2


def detect_hallucinated_element(
    tool_history: list[str],
    tool_results: list[str],
) -> StuckSignal | None:
    """Detect when the agent repeatedly tries to interact with non-existent refs.

    After 2+ consecutive browser calls that return "ref not found"-style errors,
    force a fresh snapshot so the agent sees the real page state.
    """
    if len(tool_history) < _HALLUCINATION_THRESHOLD or len(tool_results) < _HALLUCINATION_THRESHOLD:
        return None

    hallucination_streak = 0
    for i in range(len(tool_history) - 1, -1, -1):
        if i >= len(tool_results):
            break
        if tool_history[i] != "browser":
            break
        result_lower = tool_results[i].lower()
        if any(phrase in result_lower for phrase in _HALLUCINATED_REF_PHRASES):
            hallucination_streak += 1
        else:
            break

    if hallucination_streak >= _HALLUCINATION_THRESHOLD:
        logger.debug(
            "[stuck] hallucinated_element trip tool=browser consecutive=%d "
            "limit=%d outcome=blocked",
            hallucination_streak, _HALLUCINATION_THRESHOLD,
        )
        return StuckSignal(
            reason="hallucinated_element",
            tool_name="browser",
            context=(
                f"Tried to interact with non-existent refs {hallucination_streak} times. "
                f"Take a fresh snapshot to see current page elements."
            ),
            needs_browser=False,
        )
    return None


# ── Convenience: run all detectors ───────────────────────────────────

_CYCLE_WINDOW = 6


def detect_cycle(
    history: list[str],
    results: list[str],
) -> StuckSignal | None:
    """Detect an A→B→C→A→B→C rotation that makes no progress.

    2026-07-26: a specialist rotated ``upwork_get_messages`` →
    ``upwork_last_conversation`` → ``browser`` for ELEVEN MINUTES. Both
    existing detectors were structurally blind to it —
    ``detect_tool_loop`` needs the same NAME N times consecutively
    (``len(set(last_n)) != 1`` bails), and ``detect_same_result``
    compares CONSECUTIVE results, which differed by two orders of
    magnitude (257 / 92 / ~13000 chars). Only a 300s timeout stopped it.

    Deliberately conservative — a cycle detector that fires on a correct
    read path is a NEW failure mode, strictly worse than the bug. Three
    independent conditions must all hold:

      1. the window is an EXACT repetition of a 2- or 3-tool pattern
         (so a single new tool name anywhere breaks it — that's progress);
      2. the pattern has ≥2 distinct tools (single-name repetition is
         ``detect_tool_loop``'s job, and it has a better message);
      3. at least one position in the cycle returns a BYTE-IDENTICAL
         result on every repetition.

    (3) uses exact equality rather than the 0.85 similarity floor used
    elsewhere: legitimate batch reads (``search_notes`` → ``get_note``)
    produce structurally similar but non-identical results, and a
    threshold loose enough to catch them would be loose enough to kill
    them. In the incident every ``get_messages`` returned the same 257
    bytes and every ``last_conversation`` the same 92, so exact equality
    is sufficient to catch it and cheap to reason about.
    """
    if len(history) < _CYCLE_WINDOW or len(results) < _CYCLE_WINDOW:
        return None

    window = history[-_CYCLE_WINDOW:]
    window_results = results[-_CYCLE_WINDOW:]

    for period in (2, 3):
        if _CYCLE_WINDOW % period:
            continue
        pattern = window[:period]
        if len(set(pattern)) < 2:
            continue  # single-tool repetition — detect_tool_loop owns it
        reps = _CYCLE_WINDOW // period
        if any(window[i] != pattern[i % period] for i in range(_CYCLE_WINDOW)):
            continue  # not a clean repetition — a new tool broke it

        for pos in range(period):
            seen = [window_results[pos + r * period] for r in range(reps)]
            if len(set(seen)) != 1:
                continue
            tool = pattern[pos]
            logger.debug(
                "[stuck] cycle trip pattern=%s period=%d reps=%d "
                "frozen_tool=%s outcome=blocked",
                "→".join(pattern), period, reps, tool,
            )
            return StuckSignal(
                reason="cycle",
                tool_name=tool,
                context=(
                    f"Cycling through {' → '.join(pattern)} without "
                    f"progress — {tool} returned the exact same result "
                    f"{reps} times. Repeating this rotation won't change "
                    "the answer; either use what you already have or try "
                    "a genuinely different approach."
                ),
                needs_browser=False,
            )
    return None


def detect_stuck(
    tool_history: list[str],
    tool_results: list[str],
    last_result: str | None = None,
) -> StuckSignal | None:
    """Run all stuck detectors. Returns first match or None.

    Priority: CAPTCHA > repeated errors > same result > tool loop.
    """
    # CAPTCHA check on latest result
    if last_result:
        signal = detect_captcha(last_result)
        if signal:
            logger.debug("[stuck] detect_stuck resolved via=captcha")
            return signal

    # Hallucinated element: agent keeps clicking refs that don't exist
    signal = detect_hallucinated_element(tool_history, tool_results)
    if signal:
        logger.debug("[stuck] detect_stuck resolved via=hallucinated_element")
        return signal

    # No progress: last N browser actions all failed verification
    signal = detect_no_progress(tool_history, tool_results)
    if signal:
        logger.debug("[stuck] detect_stuck resolved via=no_progress")
        return signal

    # Repeated errors
    signal = detect_repeated_errors(tool_history, tool_results)
    if signal:
        logger.debug("[stuck] detect_stuck resolved via=repeated_errors")
        return signal

    # Intent flail — catches the "n8n failed → pivot to run_command / browser"
    # pattern where the plain repeated-error counter resets on tool-name change.
    signal = detect_intent_flail(tool_history, tool_results)
    if signal:
        logger.debug("[stuck] detect_stuck resolved via=intent_flail")
        return signal

    # Same result (different args but identical output — SPA not navigating, etc.)
    signal = detect_same_result(tool_results)
    if signal:
        # Enrich with tool name from history
        if tool_history:
            signal = StuckSignal(
                reason=signal.reason,
                tool_name=tool_history[-1],
                context=signal.context,
                needs_browser=signal.needs_browser,
            )
        logger.debug("[stuck] detect_stuck resolved via=same_result")
        return signal

    # Tool loop — pass results so batch reads with varying content
    # (e.g. email_get(id=X) 24× → 24 different emails) don't false-fire.
    signal = detect_tool_loop(tool_history, results=tool_results)
    if signal:
        logger.debug("[stuck] detect_stuck resolved via=tool_loop")
        return signal

    # Cycle — A→B→C→A→B→C rotations that every detector above misses
    # because no single tool NAME repeats consecutively and no two
    # CONSECUTIVE results resemble each other. Runs last: the detectors
    # above are more specific and carry better remediation text.
    signal = detect_cycle(tool_history, tool_results)
    if signal:
        logger.debug("[stuck] detect_stuck resolved via=cycle")
        return signal

    return None
