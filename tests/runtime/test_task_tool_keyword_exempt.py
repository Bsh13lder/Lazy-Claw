"""2026-07-03 task-CRUD routing incident — keyword-injected task tools
must survive SPECIALIST-FIRST stripping and THIN-ROUTER post-action
narrowing on the same turn.

Root cause (verified in agent.py, both LAZYCLAW_THIN_ROUTER=1 and
LAZYCLAW_SPECIALIST_FIRST_BRAIN=1 in prod):

  1. Task keywords ("create a task...") set ``_wants_tasks = True`` and
     inject add_task/delete_task/etc. schemas into ``tools`` (agent.py
     ~3089-3098).
  2. But the SPECIALIST-FIRST filter re-runs on EVERY iteration (incl.
     iteration 0) and strips every non-meta, non-readonly tool — task
     tools are mutations, so they never reach the LLM despite being
     injected (agent.py ~4180-4208, ``_specialist_first_filter_pass``).
  3. MiniMax still calls ``add_task``/``delete_task`` (it knows they
     exist); the call is dropped as "hallucinated" because the schema
     was never in the sent payload, and the late-inject-from-registry
     door explicitly refuses to resurrect specialist-first-filtered
     names (agent.py ~4768-4779).
  4. Depending on timing this either dead-ends in a "tool doesn't exist"
     bail message (delete case) or trips the thin-router/specialist-
     first stall failsafe into a confusing background dispatch (create
     case) — the task is never handled inline even though it was
     injected for exactly that purpose.

Fix: keyword-injected deterministic-intent tools (task keywords →
_TASK_TOOL_NAMES) are added to the existing turn-scoped
``_specialist_first_exempt`` set (the same mechanism the browser-
fallback recovery path already uses — see test_specialist_first_brain.py
fallback-exempt tests). The exempt set is consulted by:
  * the specialist-first filter pass (already parameterized on ``exempt``,
    unchanged),
  * the thin-router narrow-set computation (now unioned with the exempt
    set), and
  * the thin-router late-inject-door gate (now also checks the exempt
    set before blocking resurrection).

Non-task turns are unaffected: ``_specialist_first_exempt`` only gains
task tool names when ``_wants_tasks`` is True, and the underlying
predicates (``_specialist_first_tool_allowed``, ``_specialist_first_filter_pass``
with an empty/foreign exempt set) are untouched.
"""

from __future__ import annotations

import inspect
import re

from lazyclaw.runtime import agent as agent_mod
from lazyclaw.runtime.agent import (
    _DISPATCH_ONLY_TOOLS,
    _META_TOOLS,
    _TASK_TOOL_NAMES,
    _specialist_first_filter_pass,
    _specialist_first_tool_allowed,
)

_SRC = inspect.getsource(agent_mod)


# ── source wiring: exemption populated only on _wants_tasks ─────────


def test_task_tools_added_to_exempt_set_on_wants_tasks() -> None:
    """The exempt-set population must be gated on ``_wants_tasks`` and
    use ``_TASK_TOOL_NAMES`` — mirroring the browser-fallback exemption
    pattern, not a new routing mechanism."""
    idx = _SRC.index("_specialist_first_exempt: set[str] = set()")
    window = _SRC[idx:idx + 1200]
    assert "if _wants_tasks:" in window
    assert "_specialist_first_exempt |= _TASK_TOOL_NAMES" in window


def test_exemption_wired_before_the_main_loop() -> None:
    """Populated once per turn (not per iteration) — before the
    ``for iteration in range(max_iterations)`` loop, same timing as the
    exempt-set declaration itself."""
    decl_idx = _SRC.index("_specialist_first_exempt: set[str] = set()")
    exempt_idx = _SRC.index("_specialist_first_exempt |= _TASK_TOOL_NAMES")
    loop_idx = _SRC.index("for iteration in range(max_iterations)")
    assert decl_idx < exempt_idx < loop_idx


# ── behavioral: task tools survive the specialist-first filter ──────


def test_task_tools_still_filtered_without_exemption() -> None:
    """Baseline (proves the bug existed): with an empty/foreign exempt
    set, task tools are mutations and get filtered — unchanged from
    before this fix."""
    for name in ("add_task", "delete_task", "complete_task", "update_task"):
        assert not _specialist_first_tool_allowed(name), name
        assert not _specialist_first_filter_pass(name, set()), name


def test_task_tools_pass_filter_when_exempted() -> None:
    """With the turn-scoped exempt set populated (as the fix now does
    when _wants_tasks fires), task tools survive the specialist-first
    filter pass — the same mechanism already proven for the browser
    fallback path."""
    for name in sorted(_TASK_TOOL_NAMES):
        assert _specialist_first_filter_pass(name, _TASK_TOOL_NAMES), name


def test_exempting_task_tools_does_not_open_unrelated_domain_tools() -> None:
    """The fix must not broaden what the brain can do beyond the
    injected task-tool set — other mutating/domain tools stay filtered
    even on a task turn."""
    for name in (
        "upwork_send_message", "upwork_submit_proposal", "browser",
        "use_host_browser", "run_command", "send_email", "set_cells",
        "append_to_doc",
    ):
        assert not _specialist_first_filter_pass(name, _TASK_TOOL_NAMES), name


def test_task_tools_survive_a_live_iteration_filter_pass() -> None:
    """Simulate the iteration filter over a live tool list the way the
    runtime builds it: task tools + meta tools + an unrelated domain
    tool. Only the unrelated domain tool is stripped."""
    exempt = set(_TASK_TOOL_NAMES)
    tools = [
        {"function": {"name": n}}
        for n in (
            "search_tools", "delegate", "add_task", "delete_task",
            "list_tasks", "upwork_send_message",
        )
    ]
    kept = [
        t for t in tools
        if _specialist_first_filter_pass(
            t.get("function", {}).get("name"), exempt,
        )
    ]
    assert [t["function"]["name"] for t in kept] == [
        "search_tools", "delegate", "add_task", "delete_task", "list_tasks",
    ]


def test_add_task_not_dropped_as_hallucinated_after_filter_pass() -> None:
    """Reproduces the runtime's hallucination-drop check
    (``tc.name not in _valid_names``) against the tools list AFTER the
    specialist-first filter pass. Before the fix, add_task/delete_task
    were absent from ``_valid_names`` and every call was dropped; with
    the exempt set populated they are present."""
    exempt = set(_TASK_TOOL_NAMES)
    sent_tools = [
        {"function": {"name": n}}
        for n in ("search_tools", "delegate", "add_task", "delete_task")
        if _specialist_first_filter_pass(n, exempt)
    ]
    valid_names = {t.get("function", {}).get("name") for t in sent_tools}
    for tc_name in ("add_task", "delete_task"):
        assert tc_name in valid_names, (
            f"{tc_name} would be dropped as hallucinated — not in "
            "the sent tool payload"
        )


# ── behavioral: thin-router narrow-set exemption ─────────────────────


def test_narrow_set_computation_unions_exempt_set() -> None:
    """The THIN-ROUTER narrow-set line must union in the turn-scoped
    exempt set so keyword-injected task tools survive post-action
    narrowing, not just the specialist-first pre-filter."""
    idx = _SRC.index("if _cap_now:")
    window = _SRC[idx:idx + 1300]
    assert "_narrow_set = (" in window
    assert "_META_TOOLS if _cap_by_mutation else _DISPATCH_ONLY_TOOLS" in window
    assert "| _specialist_first_exempt" in window


def test_task_tool_survives_mutation_cap_narrowing_when_exempted() -> None:
    """Reproduce the exact narrow-set construction for the mutation-cap
    (meta-only) case: with the task tools exempted, add_task/delete_task
    remain callable even after the 1-inline-action cap engages."""
    exempt = set(_TASK_TOOL_NAMES)
    narrow_set = _META_TOOLS | exempt
    for name in ("add_task", "delete_task", "list_tasks"):
        assert name in narrow_set, name


def test_non_task_domain_tool_still_narrowed_when_task_exempt_active() -> None:
    """A task turn's exempt set must not leak into narrowing protection
    for unrelated domain tools."""
    exempt = set(_TASK_TOOL_NAMES)
    narrow_set = _META_TOOLS | exempt
    assert "upwork_send_message" not in narrow_set
    assert "browser" not in narrow_set


def test_narrow_set_unchanged_on_non_task_turn() -> None:
    """Non-task turn: exempt set is empty, so the narrow set is exactly
    what it was before this fix — no behavior change."""
    exempt: set[str] = set()
    narrow_set_mutation = _META_TOOLS | exempt
    narrow_set_budget = _DISPATCH_ONLY_TOOLS | exempt
    assert narrow_set_mutation == _META_TOOLS
    assert narrow_set_budget == _DISPATCH_ONLY_TOOLS
    assert "add_task" not in narrow_set_mutation
    assert "add_task" not in narrow_set_budget


# ── behavioral: late-inject door gate also consults the exempt set ──


def test_late_inject_thin_router_gate_checks_exempt_set() -> None:
    """The thin-router-capped branch of the late-inject door must now
    also let exempt names re-enter (defense in depth for the
    history-remembered-tool path), without weakening the existing
    _META_TOOLS check."""
    gate = re.search(
        r"_thin_router_capped\s*\n?\s*and tc\.name not in _META_TOOLS"
        r"\s*\n?\s*and tc\.name not in _specialist_first_exempt",
        _SRC,
    )
    assert gate is not None, (
        "late-inject door must exempt keyword-injected task tools from "
        "the thin-router cap gate"
    )


def test_late_inject_gate_still_blocks_non_exempt_names() -> None:
    """Direct re-derivation of the late-inject gate's boolean condition
    (mirrors the source, since it lives inline in the agent loop, not a
    standalone function) — proves the gate still blocks arbitrary
    history-remembered domain tools when they are not in the exempt
    set, and lets them through only when exempted."""
    def _blocked(tc_name: str, capped: bool, exempt: set[str]) -> bool:
        return (
            capped
            and tc_name not in _META_TOOLS
            and tc_name not in exempt
        )

    assert _blocked("upwork_send_message", True, set())
    assert _blocked("upwork_send_message", True, set(_TASK_TOOL_NAMES))
    assert not _blocked("add_task", True, set(_TASK_TOOL_NAMES))
    assert _blocked("add_task", True, set())  # non-task turn: still blocked
    assert not _blocked("search_tools", True, set())  # meta always passes


# ── non-task turn: fully unaffected (regression guard) ───────────────


def test_specialist_first_predicate_itself_is_unchanged() -> None:
    """The raw allow-predicate (no exempt context) must be byte-for-byte
    unchanged behavior — this fix only ever widens the turn-scoped
    exempt SET, never the predicate function."""
    # NOTE: list_tasks/list_jobs are excluded — they're legitimately
    # readonly (`_is_readonly_inspection` matches the "list_" prefix)
    # and already pass the filter regardless of this fix.
    for name in (
        "add_task", "delete_task", "complete_task", "update_task",
        "schedule_job", "manage_job",
        "upwork_send_message", "browser", "run_command",
    ):
        assert not _specialist_first_tool_allowed(name), name


def test_non_task_turn_exempt_set_stays_empty_per_source() -> None:
    """Source-level guard: the ``|=`` population line is INSIDE the
    ``if _wants_tasks:`` block, not at module scope or unconditional —
    a non-task turn never executes it."""
    idx = _SRC.index("_specialist_first_exempt |= _TASK_TOOL_NAMES")
    # The nearest preceding "if" must be the _wants_tasks guard.
    head = _SRC.rindex("if ", 0, idx)
    guard_line = _SRC[head:idx].splitlines()[0]
    assert "_wants_tasks" in guard_line, guard_line
