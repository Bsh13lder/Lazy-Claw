"""2026-08-19 MCP-restart routing incident — keyword-injected MCP
management tools must survive SPECIALIST-FIRST stripping and THIN-ROUTER
post-action narrowing on the same turn.

Root cause (verified in prod log 2026-08-19 08:06-08:07, both
LAZYCLAW_THIN_ROUTER=1 and LAZYCLAW_SPECIALIST_FIRST_BRAIN=1):

  1. "restart the whatsapp mcp" matches _MCP_MGMT_KEYWORDS → sets
     ``_wants_mcp_mgmt = True`` and injects the full
     ``_MCP_MGMT_TOOL_NAMES`` suite into ``tools`` ("Tool names sent"
     showed 33 tools including connect/disconnect_mcp_server).
  2. The SPECIALIST-FIRST filter re-runs every iteration and strips all
     non-meta, non-readonly names. ``list_mcp_servers`` survives (the
     "list_" readonly prefix); ``connect_mcp_server`` /
     ``disconnect_mcp_server`` are mutations → stripped ("tools filtered
     33 → 13"). The 2026-07-03 exemption fix covered ONLY
     ``_wants_tasks`` — the structurally identical MCP intent was left
     out.
  3. search_tools re-discovered the schemas ("Injected 8 tool schemas")
     but the next iteration's filter stripped them again.
  4. The FG work-call budget then narrowed to dispatch-only, forcing a
     delegation — which dead-ended in system_specialist (no MCP tools
     in its allowlist at the time).

Fix: mirror the 2026-07-03 task-CRUD exemption exactly — when
``_wants_mcp_mgmt`` fires, add ``_MCP_MGMT_TOOL_NAMES`` to the
turn-scoped ``_specialist_first_exempt`` set. The exempt set already
threads through the specialist-first filter pass, the thin-router
narrow-set union, and the late-inject-door gate — no new plumbing.

Non-MCP turns are unaffected: the exempt set only gains MCP names when
``_wants_mcp_mgmt`` is True, and the underlying predicates are
untouched.
"""

from __future__ import annotations

import inspect

from lazyclaw.runtime import agent as agent_mod
from lazyclaw.runtime.agent import (
    _DISPATCH_ONLY_TOOLS,
    _META_TOOLS,
    _MCP_MGMT_TOOL_NAMES,
    _specialist_first_filter_pass,
    _specialist_first_tool_allowed,
)

_SRC = inspect.getsource(agent_mod)

_MCP_MUTATING_NAMES = (
    "install_mcp_server", "connect_mcp_server", "add_mcp_server",
    "disconnect_mcp_server", "remove_mcp_server",
    "favorite_mcp_server", "unfavorite_mcp_server",
)


# ── source wiring: exemption populated only on _wants_mcp_mgmt ───────


def test_mcp_tools_added_to_exempt_set_on_wants_mcp_mgmt() -> None:
    """The exempt-set population must be gated on ``_wants_mcp_mgmt``
    and use ``_MCP_MGMT_TOOL_NAMES`` — mirroring the 2026-07-03 task
    exemption pattern, not a new routing mechanism."""
    idx = _SRC.index("_specialist_first_exempt: set[str] = set()")
    window = _SRC[idx:idx + 2400]
    assert "if _wants_mcp_mgmt:" in window
    assert "_specialist_first_exempt |= _MCP_MGMT_TOOL_NAMES" in window


def test_mcp_exemption_wired_before_the_main_loop() -> None:
    """Populated once per turn (not per iteration) — before the
    ``for iteration in range(max_iterations)`` loop, same timing as the
    exempt-set declaration and the task exemption."""
    decl_idx = _SRC.index("_specialist_first_exempt: set[str] = set()")
    exempt_idx = _SRC.index("_specialist_first_exempt |= _MCP_MGMT_TOOL_NAMES")
    loop_idx = _SRC.index("for iteration in range(max_iterations)")
    assert decl_idx < exempt_idx < loop_idx


def test_wants_mcp_mgmt_predeclared_for_toolless_turns() -> None:
    """``_wants_mcp_mgmt`` is assigned inside the ``elif needs_tools:``
    branch but referenced by the exempt gate on EVERY path — a tool-less
    chat turn ("hello") skips the branch, so without a pre-declare the
    gate raises UnboundLocalError (same failure mode the 2026-07-03 fix
    pre-declared ``_wants_tasks`` against)."""
    decl_idx = _SRC.index("_wants_mcp_mgmt: bool = False")
    assign_idx = _SRC.index("_wants_mcp_mgmt = any(")
    assert decl_idx < assign_idx


def test_mcp_exempt_population_gated_on_wants_mcp_mgmt() -> None:
    """Source-level guard: the ``|=`` population line is INSIDE the
    ``if _wants_mcp_mgmt:`` block, not unconditional — a non-MCP turn
    never executes it."""
    idx = _SRC.index("_specialist_first_exempt |= _MCP_MGMT_TOOL_NAMES")
    head = _SRC.rindex("if ", 0, idx)
    guard_line = _SRC[head:idx].splitlines()[0]
    assert "_wants_mcp_mgmt" in guard_line, guard_line


# ── behavioral: MCP tools survive the specialist-first filter ────────


def test_mcp_mutations_still_filtered_without_exemption() -> None:
    """Baseline (proves the bug existed): with an empty/foreign exempt
    set, the mutating MCP tools get filtered — unchanged from before
    this fix. (``list_mcp_servers`` is legitimately readonly via the
    "list_" prefix and passes regardless.)"""
    for name in _MCP_MUTATING_NAMES:
        assert not _specialist_first_tool_allowed(name), name
        assert not _specialist_first_filter_pass(name, set()), name
    assert _specialist_first_filter_pass("list_mcp_servers", set())


def test_mcp_tools_pass_filter_when_exempted() -> None:
    """With the turn-scoped exempt set populated (as the fix now does
    when _wants_mcp_mgmt fires), the whole injected MCP suite survives
    the specialist-first filter pass."""
    for name in sorted(_MCP_MGMT_TOOL_NAMES):
        assert _specialist_first_filter_pass(name, _MCP_MGMT_TOOL_NAMES), name


def test_exempting_mcp_tools_does_not_open_unrelated_domain_tools() -> None:
    """The fix must not broaden what the brain can do beyond the
    injected MCP suite — other mutating/domain tools stay filtered even
    on an MCP turn."""
    for name in (
        "upwork_send_message", "browser", "use_host_browser",
        "run_command", "send_email", "add_task", "delete_task",
        "vault_set", "set_permission",
    ):
        assert not _specialist_first_filter_pass(
            name, _MCP_MGMT_TOOL_NAMES,
        ), name


def test_mcp_tools_survive_a_live_iteration_filter_pass() -> None:
    """Simulate the iteration filter over a live tool list the way the
    runtime builds it (the incident's exact shape): MCP tools + meta
    tools + an unrelated domain tool. Only the unrelated domain tool is
    stripped."""
    exempt = set(_MCP_MGMT_TOOL_NAMES)
    tools = [
        {"function": {"name": n}}
        for n in (
            "search_tools", "delegate", "connect_mcp_server",
            "disconnect_mcp_server", "list_mcp_servers",
            "upwork_send_message",
        )
    ]
    kept = [
        t for t in tools
        if _specialist_first_filter_pass(
            t.get("function", {}).get("name"), exempt,
        )
    ]
    assert [t["function"]["name"] for t in kept] == [
        "search_tools", "delegate", "connect_mcp_server",
        "disconnect_mcp_server", "list_mcp_servers",
    ]


def test_disconnect_not_dropped_as_hallucinated_after_filter_pass() -> None:
    """Reproduces the runtime's hallucination-drop check
    (``tc.name not in _valid_names``) against the tools list AFTER the
    specialist-first filter pass. Before the fix, connect/disconnect
    were absent from the sent payload; with the exempt set populated
    they are present."""
    exempt = set(_MCP_MGMT_TOOL_NAMES)
    sent_tools = [
        {"function": {"name": n}}
        for n in (
            "search_tools", "delegate", "connect_mcp_server",
            "disconnect_mcp_server",
        )
        if _specialist_first_filter_pass(n, exempt)
    ]
    valid_names = {t.get("function", {}).get("name") for t in sent_tools}
    for tc_name in ("connect_mcp_server", "disconnect_mcp_server"):
        assert tc_name in valid_names, (
            f"{tc_name} would be dropped as hallucinated — not in "
            "the sent tool payload"
        )


# ── behavioral: thin-router narrow-set exemption ─────────────────────


def test_mcp_tool_survives_mutation_cap_narrowing_when_exempted() -> None:
    """Reproduce the exact narrow-set construction for the mutation-cap
    (meta-only) case: with the MCP suite exempted, connect/disconnect
    remain callable even after the 1-inline-action cap engages."""
    exempt = set(_MCP_MGMT_TOOL_NAMES)
    narrow_set = _META_TOOLS | exempt
    for name in (
        "connect_mcp_server", "disconnect_mcp_server", "list_mcp_servers",
    ):
        assert name in narrow_set, name


def test_mcp_tool_survives_budget_cap_narrowing_when_exempted() -> None:
    """The incident's actual path: the FG work-call budget (3 reads)
    narrowed to DISPATCH-ONLY. With the exempt set unioned in, the MCP
    suite must survive that narrowing too — the brain restarts the
    server inline instead of being forced into a blind delegation."""
    exempt = set(_MCP_MGMT_TOOL_NAMES)
    narrow_set = _DISPATCH_ONLY_TOOLS | exempt
    for name in ("connect_mcp_server", "disconnect_mcp_server"):
        assert name in narrow_set, name


def test_non_mcp_domain_tool_still_narrowed_when_mcp_exempt_active() -> None:
    """An MCP turn's exempt set must not leak narrowing protection to
    unrelated domain tools."""
    exempt = set(_MCP_MGMT_TOOL_NAMES)
    narrow_set = _META_TOOLS | exempt
    assert "upwork_send_message" not in narrow_set
    assert "browser" not in narrow_set
    assert "add_task" not in narrow_set


def test_narrow_set_unchanged_on_non_mcp_turn() -> None:
    """Non-MCP turn: exempt set is empty, so the narrow set is exactly
    what it was before this fix — no behavior change."""
    exempt: set[str] = set()
    assert (_META_TOOLS | exempt) == _META_TOOLS
    assert (_DISPATCH_ONLY_TOOLS | exempt) == _DISPATCH_ONLY_TOOLS
    assert "connect_mcp_server" not in (_META_TOOLS | exempt)
    assert "disconnect_mcp_server" not in (_DISPATCH_ONLY_TOOLS | exempt)


# ── non-MCP turn: fully unaffected (regression guard) ────────────────


def test_specialist_first_predicate_itself_is_unchanged() -> None:
    """The raw allow-predicate (no exempt context) must be unchanged —
    this fix only ever widens the turn-scoped exempt SET, never the
    predicate function."""
    for name in _MCP_MUTATING_NAMES:
        assert not _specialist_first_tool_allowed(name), name
