"""Stuck detection for agent loops — pure functions, no side effects.

Detects when the agent is stuck in a loop, hitting CAPTCHAs,
or getting repeated errors. Returns structured StuckSignal for
the agent loop to act on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class StuckSignal:
    """Immutable signal indicating the agent is stuck."""

    reason: str          # "loop", "captcha", "repeated_error"
    tool_name: str       # which tool triggered the stuck state
    context: str         # human-readable description for the user
    needs_browser: bool  # whether visible browser handoff is relevant
    url: str | None = None


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
    "browser": 8,  # Browser workflows need steps, but 20 was too many — cap at 8
    "web_search": 6,  # Research needs 3-5 searches then synthesis
    "list_directory": 3,  # Directory listing should not loop
    "default": 3,
}

# MCP tools that do batch operations (email organize, bulk label, etc.)
# These need higher limits because one "organize inbox" task = many calls.
# The same-result detector still catches true stuck loops.
_BATCH_OP_PREFIXES = ("email_", "whatsapp_", "instagram_")

def _effective_limit(tool_name: str, limits: dict[str, int]) -> int:
    """Get loop limit for a tool, with higher defaults for batch-op tools."""
    if tool_name in limits:
        return limits[tool_name]
    # MCP batch tools get 10 consecutive calls before stuck
    for prefix in _BATCH_OP_PREFIXES:
        if tool_name.startswith(prefix):
            return 10
    return limits.get("default", 3)


def detect_tool_loop(
    history: list[str],
    limits: dict[str, int] | None = None,
) -> StuckSignal | None:
    """Detect when the same tool is called N+ times consecutively.

    Browser gets a higher limit (multi-step navigation is normal).
    MCP batch tools (email_*, whatsapp_*) get 10 before stuck.
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
        return StuckSignal(
            reason="repeated_error",
            tool_name=last_tool,
            context=f"'{last_tool}' failed {error_count} times in a row.",
            needs_browser=(last_tool == "browser"),
        )
    return None


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


# ── Convenience: run all detectors ───────────────────────────────────

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
            return signal

    # Repeated errors
    signal = detect_repeated_errors(tool_history, tool_results)
    if signal:
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
        return signal

    # Tool loop
    signal = detect_tool_loop(tool_history)
    if signal:
        return signal

    return None
