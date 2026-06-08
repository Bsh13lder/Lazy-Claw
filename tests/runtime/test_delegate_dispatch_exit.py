"""`delegate` is a fire-and-forget async dispatch (delegate.py:268) — the
foreground guards must treat it exactly like `dispatch_subagents` /
`run_background`. 2026-06-08 14:18 incident: a delegate turn did not exit,
tripped the action-claim retry on a truthful 'I've dispatched', ran the
work itself, then AUTO-PROMOTE spawned a third run_background executor —
triple execution, specialist result orphaned.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def test_auto_promote_excludes_delegate() -> None:
    """The AUTO-PROMOTE trigger must skip when `delegate` was already
    called this turn (mirrors the run_background / dispatch_subagents
    exclusions)."""
    idx = _AGENT_SRC.index("_promoted_to_bg = True")
    head = _AGENT_SRC.rindex("if (", 0, idx)
    condition = _AGENT_SRC[head:idx]
    assert '"run_background" not in _called_tool_names' in condition, (
        "regression: run_background exclusion must remain"
    )
    assert '"dispatch_subagents" not in _called_tool_names' in condition, (
        "regression: dispatch_subagents exclusion must remain"
    )
    assert '"delegate" not in _called_tool_names' in condition, (
        "AUTO-PROMOTE must not re-background a turn that already "
        "delegated — delegate is already a non-blocking dispatch"
    )


def test_action_claim_guards_include_delegate() -> None:
    """The action-claim retry AND the force-dispatch failsafe must skip
    when the brain already called `delegate` — else a truthful 'I've
    dispatched' is force-rolled into duplicate inline work. Together with
    the AUTO-PROMOTE exclusion that's 3 guard sites total."""
    assert _AGENT_SRC.count(
        '"delegate" not in _called_tool_names'
    ) >= 3, (
        "expected the delegate guard in AUTO-PROMOTE + the action-claim "
        "retry + the action-claim force-dispatch failsafe (3 sites)"
    )


def test_delegate_has_dispatch_and_exit_hardstop() -> None:
    """After a successful delegate, the loop must hand off + return (mirror
    the run_background hard-stop) so the brain frees itself."""
    marker = "Hard stop: delegate dispatched"
    assert marker in _AGENT_SRC, (
        "delegate dispatch-and-exit hard-stop block must exist"
    )
    didx = _AGENT_SRC.index(marker)
    window = _AGENT_SRC[didx:didx + 2000]
    assert 'tc.name == "delegate"' in window, (
        "delegate hard-stop must key on the delegate tool call"
    )
    assert "exiting foreground turn" in window, (
        "delegate hard-stop must log the foreground-turn handoff"
    )
    assert "return result" in window, (
        "delegate hard-stop must return the delegate result from the loop"
    )
