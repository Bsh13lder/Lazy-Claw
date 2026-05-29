"""AUTO-PROMOTE must not re-background a turn that already called
`dispatch_subagents`.

2026-05-29 "Chek my whats up + chek mail" incident: the foreground turn
called `email_read` + `dispatch_subagents`. Because `dispatch_subagents`
is not a read-only inspection, `_only_readonly_so_far` went False and
AUTO-PROMOTE fired, re-submitting the whole message to a background
worker. That worker then called `dispatch_subagents` AGAIN — a nested
fan-out whose subagent results route to `pending_subagent_notes` that
never drain, so the user got a future-tense promise and no data.

`dispatch_subagents` is ALREADY a non-blocking async dispatch (it returns
immediately with task IDs). Re-promoting a turn that called it to a
background task is redundant and harmful, exactly like the existing
`run_background` exclusion. The AUTO-PROMOTE trigger must exclude it.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def test_auto_promote_excludes_dispatch_subagents() -> None:
    """The AUTO-PROMOTE trigger block must skip when dispatch_subagents
    was already called this turn (mirrors the run_background exclusion)."""
    idx = _AGENT_SRC.index("_promoted_to_bg = True")
    # Walk backwards to the start of the `if (` condition guarding it.
    head = _AGENT_SRC.rindex("if (", 0, idx)
    condition = _AGENT_SRC[head:idx]
    assert '"run_background" not in _called_tool_names' in condition, (
        "regression: run_background exclusion must remain"
    )
    assert '"dispatch_subagents" not in _called_tool_names' in condition, (
        "AUTO-PROMOTE must not re-background a turn that already "
        "dispatched subagents (the nested-fanout-strands-results bug)"
    )


def test_action_claim_retry_skips_when_already_dispatched() -> None:
    """The broadened _ACTION_CLAIM_RE now matches legit dispatch statuses
    ("I'll send the consolidated result shortly"). The action-claim retry
    must therefore skip when the brain ALREADY called dispatch_subagents /
    run_background this turn — otherwise it force-rolls / re-dispatches a
    truthful status. Both the retry and the force-dispatch failsafe must
    carry the guard."""
    # Count occurrences near the two action-claim guard sites.
    assert _AGENT_SRC.count(
        '"dispatch_subagents" not in _called_tool_names'
    ) >= 3, (
        "expected the dispatch_subagents guard in AUTO-PROMOTE + the "
        "action-claim retry + the action-claim force-dispatch failsafe"
    )
