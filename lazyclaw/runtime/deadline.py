"""Deadline awareness for workers — budget notes + progress-based extension.

2026-08-18 himap research incident: browser_specialist made 63 SUCCESSFUL
tool calls and was hard-killed at 480s mid-step; the research fallback died
identically at 420s. Every "ask for help" trigger keys on failure signals,
and nothing ever failed — slow success is invisible to them. Worse, a
timeout evaporates everything the worker gathered.

This module gives runners Claude Code semantics:

- ``budget_note`` — two one-shot system notes: at HALF budget ("pace
  yourself") and inside the FINAL window ("stop researching, synthesize what
  you have NOW"). Partial results beat vanished work.
- ``should_extend`` — when the deadline fires but the worker produced a tool
  result recently, the runner grants a bounded extension instead of killing
  mid-work. The user's stop button (cancel token) remains the true kill; the
  extension cap keeps a hard runaway ceiling.
"""

from __future__ import annotations

# Seconds before the deadline at which the worker is told to synthesize.
FINAL_WINDOW_S = 60

# One extension grants this much extra time.
EXTENSION_S = 240

# Hard cap on extensions — worst case = budget + MAX_EXTENSIONS * EXTENSION_S.
MAX_EXTENSIONS = 2

# A tool result younger than this counts as "still progressing".
PROGRESS_FRESH_S = 90

_HALF_NOTE = (
    "TIME BUDGET: about half your time is gone. Pace yourself — prefer "
    "FINISHING the task over further exploration. If you cannot finish "
    "everything, prioritize the deliverable and cut optional steps."
)

_FINAL_NOTE = (
    "TIME BUDGET — FINAL WINDOW: less than a minute remains. STOP starting "
    "new research. Synthesize everything you have into your final report "
    "NOW — partial findings with honest gaps beat a timeout that reports "
    "nothing. If you genuinely cannot conclude, state exactly what you "
    "found and what is missing."
)


def budget_note(
    elapsed_s: float,
    budget_s: float | None,
    notes_sent: set,
) -> str | None:
    """One-shot budget note for the current iteration, or None.

    ``notes_sent`` is mutated: "half" / "final" markers are added when the
    corresponding note is returned, so each fires at most once per run.
    """
    if not budget_s or budget_s <= 0:
        return None
    if elapsed_s >= budget_s - FINAL_WINDOW_S and "final" not in notes_sent:
        notes_sent.add("final")
        return _FINAL_NOTE
    if elapsed_s >= budget_s / 2 and "half" not in notes_sent:
        notes_sent.add("half")
        return _HALF_NOTE
    return None


def should_extend(
    progress_age_s: "float | None",
    extensions_used: int,
) -> bool:
    """True when a timed-out worker deserves more time instead of a kill.

    Fresh progress (a tool result within ``PROGRESS_FRESH_S``) and extensions
    remaining → extend. ``None`` means the worker never produced ANY progress
    — the deadline itself is the verdict, never extend. Stale progress means
    the worker is spinning or the provider is wedged — extension would only
    delay the honest failure.
    """
    if progress_age_s is None:
        return False
    if extensions_used >= MAX_EXTENSIONS:
        return False
    return progress_age_s <= PROGRESS_FRESH_S
