"""Failure-aware guidance for the brain's fan-out consolidation turn.

When a subagent in a brain fan-out group fails (structured
``[SPECIALIST FAILURE REPORT]`` or plain error), the consolidation turn
must act like the Claude Code orchestrator: decide the next move, never
ship a false "✅ done" or a vague apology. This module owns the two pure
pieces task_runner injects/checks:

* :func:`build_failure_guidance` — the compact instruction block appended
  to the synthetic consolidation prompt (re-delegate ONCE / answer from
  partials / report the blocker precisely).
* :func:`draft_claims_success` — cheap success-claim detector for the
  observation-only ``[consolidation-coherence]`` WARNING (no rewrite loop
  — the F1 machinery owns rewrites elsewhere).
"""

from __future__ import annotations

import re

FAILURE_GUIDANCE_HEADER = "[ORCHESTRATOR GUIDANCE — SUBAGENT FAILURE]"

# Log tag for the observation-only no-false-✅ guard in task_runner.
COHERENCE_LOG_TAG = "[consolidation-coherence]"

_GUIDANCE_COMMON_HEAD = (
    f"{FAILURE_GUIDANCE_HEADER}\n"
    "At least one subagent FAILED — its structured failure report is above.\n"
    "You are the orchestrator. Pick EXACTLY ONE move:\n"
)
_GUIDANCE_COMMON_TAIL = (
    "NEVER claim success or say the task is \"done\" — a failure is present.\n"
    "Never reply with a vague apology."
)

_GUIDANCE_WITH_REDELEGATE = (
    _GUIDANCE_COMMON_HEAD
    + "(a) Re-delegate the FAILED work to a DIFFERENT specialist whose "
    "toolset fits (call `delegate`), avoiding the tools that failed. "
    "ONE retry only — if `delegate` is unavailable this turn, use (b)/(c).\n"
    "(b) Answer from the partial results you DO have, explicitly flagging "
    "what is missing.\n"
    "(c) Report the blocker precisely: which tool failed, why (quote its "
    "last_error), and the concrete next action you or the user can take.\n"
    + _GUIDANCE_COMMON_TAIL
)

_GUIDANCE_BUDGET_EXHAUSTED = (
    _GUIDANCE_COMMON_HEAD
    + "The re-delegation budget is EXHAUSTED (1 retry already used). "
    "Do NOT delegate or dispatch again. Choose:\n"
    "(b) Answer from the partial results you DO have, explicitly flagging "
    "what is missing.\n"
    "(c) Report the blocker precisely: which tool failed, why (quote its "
    "last_error), and the concrete next action you or the user can take.\n"
    + _GUIDANCE_COMMON_TAIL
)


def build_failure_guidance(*, can_redelegate: bool) -> str:
    """Return the orchestrator instruction block for a failed fan-out.

    ``can_redelegate`` is True only on the first consolidation round for
    the originating turn — the retry budget caps specialist ping-pong at
    one follow-up round (see TaskRunner._claim_retry_round).
    """
    if can_redelegate:
        return _GUIDANCE_WITH_REDELEGATE
    return _GUIDANCE_BUDGET_EXHAUSTED


# Success-claim shapes. Deliberately cheap — this feeds an
# observation-only WARNING, so a rare false negative is acceptable and a
# negation guard below keeps honest "could not be completed" drafts from
# false-firing.
_SUCCESS_CLAIM_RE = re.compile(
    r"(✅|\b(?:done|completed?|finished|succeeded|successfully|all\s+set)\b)",
    re.IGNORECASE,
)

# Negation within the ~60 chars before a claim token defuses it:
# "could not be completed", "wasn't able to finish", "failed to complete".
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|isn'?t|wasn'?t|aren'?t|weren'?t|couldn'?t|can'?t|"
    r"cannot|won'?t|didn'?t|don'?t|failed\s+to|unable\s+to|without)\b"
    r"[^.!?\n]{0,50}$",
    re.IGNORECASE,
)


def draft_claims_success(draft: str | None) -> bool:
    """True when the consolidation draft positively claims success."""
    if not draft:
        return False
    for match in _SUCCESS_CLAIM_RE.finditer(draft):
        prefix = draft[max(0, match.start() - 60) : match.start()]
        if _NEGATION_RE.search(prefix):
            continue
        return True
    return False
