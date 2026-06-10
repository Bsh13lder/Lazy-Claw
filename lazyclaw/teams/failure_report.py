"""Structured specialist failure reports — pure functions, no side effects.

When a specialist run terminates failure-shaped (stuck loop, exception,
cancellation, every-tool-errored), the partial text it produced is opaque
to the brain's consolidation turn: it either ships a false "✅ done" or a
vague apology (2026-06-10 00:33 ``freelance_specialist`` stuck-loop
incident). This module renders the tracking data the runner already has
(tool history + results) into a clearly-delimited text block, following
the Claude Code orchestrator pattern: the subagent's failure returns to
the orchestrator as structured data, and the orchestrator decides the
next move (re-delegate / answer from partials / report the blocker).

Consumed by :mod:`lazyclaw.teams.runner` (which builds the report) and
:mod:`lazyclaw.runtime.task_runner` (which detects the marker and injects
orchestrator guidance into the consolidation prompt). Deliberately
import-free of both so it can never create a cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

FAILURE_REPORT_MARKER = "[SPECIALIST FAILURE REPORT]"
FAILURE_REPORT_END_MARKER = "[/SPECIALIST FAILURE REPORT]"

# Canonical outcome values. ``stuck_*`` variants beyond ``stuck_loop``
# (e.g. ``stuck_repeated_error``) are derived from StuckSignal.reason by
# the runner; consumers should treat any ``stuck_`` prefix as the
# stuck-detector family.
OUTCOME_STUCK_LOOP = "stuck_loop"
OUTCOME_EXCEPTION = "exception"
OUTCOME_ALL_TOOLS_FAILED = "all_tools_failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CANCELLED = "cancelled"

# Field caps keep the report compact — it rides inside the synthetic
# consolidation prompt, which is aggressively budget-conscious.
_TASK_PREVIEW_CAP = 200
_ERROR_TAIL_CAP = 200
_PROGRESS_CAP = 200

# Same convention the runner / stuck detector use for error-shaped
# tool results.
_ERROR_PREFIX = "Error"


def _collapse(value: object) -> str:
    """Coerce to a single-line string (whitespace collapsed)."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # pragma: no cover — str() pathology
        text = "<unrenderable>"
    return " ".join(text.split())


def _is_error_result(result: object) -> bool:
    return _collapse(result).startswith(_ERROR_PREFIX)


@dataclass(frozen=True)
class ToolAttempt:
    """Aggregated attempts for one tool within a specialist run."""

    name: str
    count: int
    # Tail of the error from the MOST RECENT call; empty when that call
    # succeeded (a tool that erred then recovered is not a blocker).
    last_error: str = ""


def build_tool_attempts(
    tool_history: Sequence[object],
    tool_results: Sequence[object],
) -> tuple[ToolAttempt, ...]:
    """Aggregate per-tool call counts + most-recent error, first-seen order.

    ``tool_history`` and ``tool_results`` are the runner's parallel
    tracking lists; index ``i`` of one corresponds to index ``i`` of the
    other. Mismatched lengths (crash mid-iteration) degrade gracefully —
    missing results are treated as empty, never raised on.
    """
    order: list[str] = []
    counts: dict[str, int] = {}
    last_errors: dict[str, str] = {}
    for i, raw_name in enumerate(tool_history):
        name = _collapse(raw_name) or "<unknown>"
        if name not in counts:
            order.append(name)
            counts[name] = 0
        counts[name] += 1
        result = tool_results[i] if i < len(tool_results) else ""
        if _is_error_result(result):
            # Tail — error messages bury the root cause at the end.
            last_errors[name] = _collapse(result)[-_ERROR_TAIL_CAP:]
        else:
            last_errors[name] = ""
    return tuple(
        ToolAttempt(name=n, count=counts[n], last_error=last_errors.get(n, ""))
        for n in order
    )


def all_tools_failed(
    tool_history: Sequence[object],
    tool_results: Sequence[object],
) -> bool:
    """True when at least one tool ran and EVERY result was error-shaped."""
    if not tool_history:
        return False
    paired = list(tool_results[: len(tool_history)])
    if not paired:
        return False
    return all(_is_error_result(r) for r in paired)


def last_progress(
    tool_history: Sequence[object],
    tool_results: Sequence[object],
) -> str:
    """Summary of the most recent SUCCESSFUL tool result, '' if none."""
    paired = min(len(tool_history), len(tool_results))
    for i in range(paired - 1, -1, -1):
        result = tool_results[i]
        if not _is_error_result(result):
            name = _collapse(tool_history[i]) or "<unknown>"
            return f"{name}: {_collapse(result)[:_PROGRESS_CAP]}"
    return ""


def render_failure_report(
    *,
    specialist: str,
    task: object,
    outcome: str,
    tool_history: Sequence[object] = (),
    tool_results: Sequence[object] = (),
    detail: str = "",
) -> str:
    """Render the delimited failure-report block.

    Never raises — a report bug must not mask the original failure. On
    internal error the per-tool sections degrade to placeholders while
    the marker + outcome lines always survive.
    """
    try:
        attempts = build_tool_attempts(tool_history, tool_results)
        progress = last_progress(tool_history, tool_results)
    except Exception:
        logger.debug("failure-report aggregation failed", exc_info=True)
        attempts, progress = (), ""

    lines = [
        FAILURE_REPORT_MARKER,
        f"specialist: {_collapse(specialist)}",
        f"task: {_collapse(task)[:_TASK_PREVIEW_CAP]}",
        f"outcome: {_collapse(outcome)}",
    ]
    if detail:
        lines.append(f"detail: {_collapse(detail)[:_ERROR_TAIL_CAP]}")
    if attempts:
        lines.append("tools_attempted:")
        for a in attempts:
            if a.last_error:
                lines.append(f"  - {a.name} (x{a.count}, last_error: {a.last_error})")
            else:
                lines.append(f"  - {a.name} (x{a.count}, last call ok)")
    else:
        lines.append("tools_attempted: (none)")
    lines.append(f"last_progress: {progress or '(none)'}")
    lines.append(FAILURE_REPORT_END_MARKER)
    return "\n".join(lines)


def extract_failure_report(text: str | None) -> str:
    """Pull the delimited report block out of a larger result text.

    Used by the consolidator when a success-shaped result carries an
    appended ``all_tools_failed`` report that the preview truncation
    would otherwise chop off. Returns '' when no marker is present.
    """
    if not text:
        return ""
    start = text.find(FAILURE_REPORT_MARKER)
    if start == -1:
        return ""
    end = text.find(FAILURE_REPORT_END_MARKER, start)
    if end == -1:
        # Unterminated block — take the rest, it's still structured data.
        return text[start:]
    return text[start : end + len(FAILURE_REPORT_END_MARKER)]
