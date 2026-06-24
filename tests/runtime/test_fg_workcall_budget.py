"""Foreground work-call budget — brake the read-heavy inline grind.

Under LAZYCLAW_THIN_ROUTER + LAZYCLAW_SPECIALIST_FIRST_BRAIN the only cap
on the brain's inline tool-calling was the 1-mutation thin-router cap and
AUTO-PROMOTE — both keyed on a MUTATION, so a read-only multi-step turn
(2026-06-24 "check workspace mcp", 8 inline read iterations) tripped
nothing and ground toward max_tool_iterations=50. The work-call budget arms
the SAME thin-router cap after _FG_WORK_CALL_BUDGET non-meta calls so the
brain is forced to delegate the rest.
"""

from __future__ import annotations

from lazyclaw.runtime.agent import (
    _FG_WORK_CALL_BUDGET,
    _should_thin_router_cap,
)


def test_budget_is_three():
    """1-2 tool reads stay inline; the 4th work call forces dispatch."""
    assert _FG_WORK_CALL_BUDGET == 3


def test_no_cap_when_thin_router_off():
    cap, reason = _should_thin_router_cap(
        thin_router=False, force_dispatch_only=False, has_tools=True,
        cap_by_mutation=True, work_calls=99,
    )
    assert cap is False and reason is None


def test_no_cap_when_dispatch_already_forced():
    cap, reason = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=True, has_tools=True,
        cap_by_mutation=True, work_calls=99,
    )
    assert cap is False and reason is None


def test_no_cap_without_tools():
    cap, reason = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=False, has_tools=False,
        cap_by_mutation=True, work_calls=99,
    )
    assert cap is False and reason is None


def test_cap_on_mutation():
    cap, reason = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=False, has_tools=True,
        cap_by_mutation=True, work_calls=0,
    )
    assert cap is True
    assert reason == "1 inline action"


def test_cap_on_budget_without_mutation():
    """THE FIX: 3 read/work calls with NO mutation still caps."""
    cap, reason = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=False, has_tools=True,
        cap_by_mutation=False, work_calls=3,
    )
    assert cap is True
    assert reason is not None and "work calls" in reason


def test_no_cap_below_budget():
    """1-2 read calls stay inline (fast 'check my upwork messages')."""
    for n in (0, 1, 2):
        cap, reason = _should_thin_router_cap(
            thin_router=True, force_dispatch_only=False, has_tools=True,
            cap_by_mutation=False, work_calls=n,
        )
        assert cap is False, f"work_calls={n} should not cap"
        assert reason is None


def test_mutation_takes_precedence_over_budget():
    cap, reason = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=False, has_tools=True,
        cap_by_mutation=True, work_calls=5,
    )
    assert cap is True
    assert reason == "1 inline action"


def test_budget_boundary_exact():
    below = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=False, has_tools=True,
        cap_by_mutation=False, work_calls=_FG_WORK_CALL_BUDGET - 1,
    )
    at = _should_thin_router_cap(
        thin_router=True, force_dispatch_only=False, has_tools=True,
        cap_by_mutation=False, work_calls=_FG_WORK_CALL_BUDGET,
    )
    assert below[0] is False
    assert at[0] is True
