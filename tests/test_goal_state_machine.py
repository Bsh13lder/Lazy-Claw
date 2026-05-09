"""Pure state-machine tests for lazyclaw.runtime.goal_executor.

No DB, no LLM — only :func:`_assert_transition` + the dataclass invariants.
Goal/Step shape, plan envelope round-trip, and terminal-state semantics.
"""

from __future__ import annotations

import json

import pytest

from lazyclaw.runtime.goal_executor import (
    Goal,
    GoalStep,
    GoalStatus,
    InvalidGoalTransition,
    _assert_transition,
    _plan_envelope,
    _plan_from_envelope,
    _TERMINAL_STATES,
    _VALID_TRANSITIONS,
)


# ── Transitions ──────────────────────────────────────────────────────


@pytest.mark.parametrize("old,new", sorted(_VALID_TRANSITIONS, key=str))
def test_valid_transitions_allowed(old: GoalStatus, new: GoalStatus):
    # Should not raise.
    _assert_transition(old, new)


@pytest.mark.parametrize("status", list(GoalStatus))
def test_self_transition_is_idempotent(status: GoalStatus):
    _assert_transition(status, status)


def test_drafting_to_done_is_blocked():
    # You can't skip EXECUTING — must go through it.
    with pytest.raises(InvalidGoalTransition):
        _assert_transition(GoalStatus.DRAFTING, GoalStatus.DONE)


def test_done_is_terminal():
    for target in [
        GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
        GoalStatus.EXECUTING, GoalStatus.BLOCKED,
        GoalStatus.FAILED, GoalStatus.ABORTED,
    ]:
        with pytest.raises(InvalidGoalTransition):
            _assert_transition(GoalStatus.DONE, target)


def test_aborted_is_terminal():
    for target in [
        GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
        GoalStatus.EXECUTING, GoalStatus.BLOCKED,
        GoalStatus.DONE, GoalStatus.FAILED,
    ]:
        with pytest.raises(InvalidGoalTransition):
            _assert_transition(GoalStatus.ABORTED, target)


def test_failed_is_terminal():
    for target in [
        GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
        GoalStatus.EXECUTING, GoalStatus.BLOCKED,
        GoalStatus.DONE, GoalStatus.ABORTED,
    ]:
        with pytest.raises(InvalidGoalTransition):
            _assert_transition(GoalStatus.FAILED, target)


def test_blocked_can_resume_to_executing():
    # Critical recovery edge: BLOCKED → EXECUTING when user unblocks.
    _assert_transition(GoalStatus.BLOCKED, GoalStatus.EXECUTING)


def test_blocked_can_request_more_info():
    # Or back to AWAITING_USER_INFO if the block was actually a missing answer.
    _assert_transition(GoalStatus.BLOCKED, GoalStatus.AWAITING_USER_INFO)


def test_terminal_set_membership():
    assert GoalStatus.DONE in _TERMINAL_STATES
    assert GoalStatus.FAILED in _TERMINAL_STATES
    assert GoalStatus.ABORTED in _TERMINAL_STATES
    assert GoalStatus.EXECUTING not in _TERMINAL_STATES
    assert GoalStatus.BLOCKED not in _TERMINAL_STATES


# ── Goal / Step dataclass invariants ─────────────────────────────────


def test_goal_is_frozen():
    g = Goal(id="x", user_id="u", title="t", status=GoalStatus.DRAFTING)
    with pytest.raises(Exception):
        g.title = "y"  # type: ignore[misc]


def test_goal_step_counters_derive_from_plan():
    plan = (
        GoalStep(idx=0, description="a", status="done"),
        GoalStep(idx=1, description="b", status="done"),
        GoalStep(idx=2, description="c", status="pending"),
    )
    g = Goal(id="x", user_id="u", title="t",
             status=GoalStatus.EXECUTING, plan=plan)
    assert g.steps_total == 3
    assert g.steps_done == 2


def test_goal_step_round_trip():
    s = GoalStep(idx=2, description="click submit", tool_hint="browser",
                 status="done", started_at=100.0, completed_at=110.0)
    payload = s.to_dict()
    s2 = GoalStep.from_dict(payload)
    assert s == s2


def test_goal_is_terminal_helper():
    for term in _TERMINAL_STATES:
        g = Goal(id="x", user_id="u", title="t", status=term)
        assert g.is_terminal()
    for live in [GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
                 GoalStatus.EXECUTING, GoalStatus.BLOCKED]:
        g = Goal(id="x", user_id="u", title="t", status=live)
        assert not g.is_terminal()


# ── Plan envelope serialization ──────────────────────────────────────


def test_plan_envelope_round_trip_preserves_fields():
    plan = (
        GoalStep(idx=0, description="open hirossa.com"),
        GoalStep(idx=1, description="add product", tool_hint="browser"),
    )
    g = Goal(
        id="abc", user_id="u", title="sell on hirossa",
        status=GoalStatus.AWAITING_USER_INFO,
        plan=plan,
        questions_pending=("which product?", "what price?"),
        answers={"hirossa_login_email": "a@b.c"},
        risks=("payment requires user approval",),
        confidence="medium",
        summary="Add and publish a single product on Hirossa.",
    )
    payload = _plan_envelope(g)
    parsed = _plan_from_envelope(payload)
    assert parsed["summary"] == g.summary
    assert parsed["confidence"] == "medium"
    assert len(parsed["steps"]) == 2
    assert parsed["steps"][1]["description"] == "add product"
    assert parsed["questions_pending"] == ["which product?", "what price?"]
    assert parsed["answers"]["hirossa_login_email"] == "a@b.c"
    assert parsed["risks"] == ["payment requires user approval"]


def test_plan_from_envelope_handles_garbage():
    assert _plan_from_envelope(None) == {}
    assert _plan_from_envelope("") == {}
    assert _plan_from_envelope("not-json") == {}
    # Valid JSON but unexpected shape — caller still gets a dict.
    assert _plan_from_envelope(json.dumps({"foo": 1})) == {"foo": 1}
