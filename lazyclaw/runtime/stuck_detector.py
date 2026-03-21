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
DEFAULT_LOOP_LIMITS: dict[str, int] = {
    "browser": 5,
    "default": 3,
}


def detect_tool_loop(
    history: list[str],
    limits: dict[str, int] | None = None,
) -> StuckSignal | None:
    """Detect when the same tool is called N+ times consecutively.

    Browser gets a higher limit (multi-step navigation is normal).
    """
    if not history:
        return None

    effective_limits = {**DEFAULT_LOOP_LIMITS, **(limits or {})}
    last_tool = history[-1]
    limit = effective_limits.get(last_tool, effective_limits["default"])

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


# ── Convenience: run all detectors ───────────────────────────────────

def detect_stuck(
    tool_history: list[str],
    tool_results: list[str],
    last_result: str | None = None,
) -> StuckSignal | None:
    """Run all stuck detectors. Returns first match or None.

    Priority: CAPTCHA > repeated errors > tool loop.
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

    # Tool loop
    signal = detect_tool_loop(tool_history)
    if signal:
        return signal

    return None
