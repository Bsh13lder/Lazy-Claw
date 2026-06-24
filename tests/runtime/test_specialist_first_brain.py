"""ADR-0005 Phase 5a — specialist-first brain (flag-gated, default off).

Behind LAZYCLAW_SPECIALIST_FIRST_BRAIN the brain's toolset is filtered on
EVERY iteration (including iteration 0) to meta tools (_META_TOOLS) plus
read-only inspections (_is_readonly_inspection). Mutating/domain tools are
never offered inline — the brain must delegate/dispatch them. Quick reads
("check my upwork messages") stay fast and inline.

Composes with the existing thin-router cap (filter first, cap after); the
late-inject-from-registry door applies the same filter; AUTO-PROMOTE is
skipped under the flag (same exclusion as _thin_router). Default off →
zero behavior change. No existing defense is deleted.
"""

from __future__ import annotations

import inspect
import re

from lazyclaw.runtime import agent as agent_mod
from lazyclaw.runtime.agent import (
    _META_TOOLS,
    _QUICK_INLINE_BUDGET_WRITES,
    _SPECIALIST_FIRST_BRAIN_ENV,
    _is_readonly_inspection,
    _specialist_first_enabled,
    _specialist_first_filter_pass,
    _specialist_first_tool_allowed,
)

_SRC = inspect.getsource(agent_mod)


# ── flag wiring (default off = zero behavior change) ────────────────


def test_flag_env_name_follows_repo_convention() -> None:
    assert _SPECIALIST_FIRST_BRAIN_ENV == "LAZYCLAW_SPECIALIST_FIRST_BRAIN"
    # Flag must be read through the shared parser helper.
    assert "_SPECIALIST_FIRST_BRAIN_ENV" in _SRC
    assert "_specialist_first = _specialist_first_enabled(" in _SRC


def test_flag_parsing_truthy_values() -> None:
    """Same accepted truthy set as LAZYCLAW_THIN_ROUTER."""
    for raw in ("1", "true", "yes", "on", " 1 ", "TRUE", "Yes", "ON"):
        assert _specialist_first_enabled(raw), raw


def test_flag_parsing_falsy_and_malformed_means_off() -> None:
    """Unset/empty/malformed env values must be OFF = zero behavior
    change (the filter is gated on this boolean, so OFF means the tool
    list is never touched)."""
    for raw in (None, "", "0", "false", "no", "off", "banana", " ", "2"):
        assert not _specialist_first_enabled(raw), repr(raw)


def test_filter_block_gated_on_flag() -> None:
    """Every specialist-first code path must be gated on _specialist_first
    so the default-off case is a zero-behavior-change no-op."""
    # The iteration filter block.
    fidx = _SRC.index("SPECIALIST-FIRST: tools filtered")
    head = _SRC.rindex("if _specialist_first", 0, fidx)
    assert fidx - head < 1600, (
        "the filter block must be gated on the _specialist_first flag"
    )


# ── the filter predicate (meta + readonly pass, mutating filtered) ──


def test_meta_tools_pass_filter() -> None:
    for name in sorted(_META_TOOLS):
        assert _specialist_first_tool_allowed(name), name


def test_readonly_inspections_pass_filter() -> None:
    for name in (
        "upwork_last_conversation",
        "upwork_inbox_check",
        "whatsapp_read",
        "whatsapp_list_chats",
        "email_read",
        "list_tasks",
        "get_project",
        "read_sheet",
        "vault_get",
        "mcp_bab07595-9b21-441c-84f7-1486df4dbf38_upwork_get_messages",
    ):
        assert _is_readonly_inspection(name), name
        assert _specialist_first_tool_allowed(name), name


def test_mutating_domain_tools_are_filtered() -> None:
    for name in (
        "upwork_send_message",
        "upwork_submit_proposal",
        "upwork_accept_offer",
        "browser",
        "use_host_browser",
        "run_command",
        "send_email",
        "set_cells",
        "append_to_doc",
        "schedule_job",
        "add_task",
        "mcp_bab07595-9b21-441c-84f7-1486df4dbf38_upwork_send_message",
    ):
        assert not _specialist_first_tool_allowed(name), name


def test_quick_inline_budget_writes_are_filtered() -> None:
    """_QUICK_INLINE_BUDGET_WRITES are MUTATIONS that ride the readonly
    predicate only to dodge AUTO-PROMOTE latency (2026-05-28) — the
    specialist-first filter must NOT offer them inline, while
    _is_readonly_inspection itself stays untouched (AUTO-PROMOTE
    behavior identical)."""
    assert _QUICK_INLINE_BUDGET_WRITES, "budget-write set must be non-empty"
    for name in sorted(_QUICK_INLINE_BUDGET_WRITES):
        # AUTO-PROMOTE exemption unchanged: still "readonly" there.
        assert _is_readonly_inspection(name), name
        # ...but specialist-first filters the write out.
        assert not _specialist_first_tool_allowed(name), name


def test_mcp_wrapped_budget_write_is_filtered() -> None:
    assert not _specialist_first_tool_allowed(
        "mcp_bab07595-9b21-441c-84f7-1486df4dbf38_add_expense"
    )


def test_empty_name_is_filtered() -> None:
    assert not _specialist_first_tool_allowed(None)
    assert not _specialist_first_tool_allowed("")


# ── browser-fallback exemption (failure recovery wins over filter) ──


def test_fallback_exempt_names_pass_filter() -> None:
    """Under the flag, after the browser-fallback injection (2 channel-MCP
    failures), `browser` must survive the next-iteration filter — the
    deliberate failure-recovery path wins over specialist-first."""
    exempt = {"browser", "use_host_browser"}
    assert not _specialist_first_tool_allowed("browser")
    assert _specialist_first_filter_pass("browser", exempt)
    assert _specialist_first_filter_pass("use_host_browser", exempt)


def test_exempt_set_does_not_open_other_tools() -> None:
    exempt = {"browser", "use_host_browser"}
    assert not _specialist_first_filter_pass("upwork_send_message", exempt)
    assert not _specialist_first_filter_pass("run_command", exempt)
    assert not _specialist_first_filter_pass(None, exempt)
    assert not _specialist_first_filter_pass("", exempt)


def test_empty_exempt_matches_plain_filter() -> None:
    for name in (
        "browser", "upwork_send_message", "list_tasks",
        "delegate", "upwork_last_conversation", "add_expense",
    ):
        assert _specialist_first_filter_pass(name, set()) == (
            _specialist_first_tool_allowed(name)
        ), name


def test_browser_survives_next_iteration_filter_after_fallback() -> None:
    """Simulate the iteration filter over a live tool list after the
    fallback block registered its exemptions: browser is kept."""
    exempt = {"browser", "use_host_browser"}  # set by the fallback block
    tools = [
        {"function": {"name": n}}
        for n in ("search_tools", "delegate", "browser", "upwork_send_message")
    ]
    kept = [
        t for t in tools
        if _specialist_first_filter_pass(
            t.get("function", {}).get("name"), exempt,
        )
    ]
    assert [t["function"]["name"] for t in kept] == [
        "search_tools", "delegate", "browser",
    ]


def test_fallback_block_registers_exempt_names() -> None:
    """The browser-fallback re-injection block must add the lifted names
    to the turn-scoped exempt set, or the [FALLBACK] guidance advertises
    a tool the next iteration silently strips again."""
    start = _SRC.index("# ── Browser fallback re-injection ──")
    end = _SRC.index("Browser fallback injected after 2 failures", start)
    block = _SRC[start:end]
    assert "_specialist_first_exempt" in block


def test_exempt_set_initialized_per_turn() -> None:
    assert "_specialist_first_exempt: set[str] = set()" in _SRC


def test_filter_block_consults_exempt_set() -> None:
    fidx = _SRC.index("SPECIALIST-FIRST: tools filtered")
    window = _SRC[max(0, fidx - 1600):fidx + 400]
    assert "_specialist_first_filter_pass(" in window
    assert "_specialist_first_exempt" in window


# ── iteration filter block (every iteration, incl. iteration 0) ─────


def test_filter_runs_before_thin_router_cap() -> None:
    """Filter first, cap after — the SPECIALIST-FIRST filter block must
    sit BEFORE the THIN-ROUTER narrowing block inside the loop."""
    filter_idx = _SRC.index("SPECIALIST-FIRST: tools filtered")
    cap_idx = _SRC.index("THIN-ROUTER: tools narrowed")
    assert filter_idx < cap_idx


def test_filter_log_line_greppable() -> None:
    assert "SPECIALIST-FIRST: tools filtered" in _SRC
    assert "meta+readonly only" in _SRC
    assert "domain work must be" in _SRC


def test_filter_suppresses_removed_names() -> None:
    """Removed names must enter _suppressed_tool_names so the late-inject
    path can't resurrect tools that WERE in the sent list."""
    fidx = _SRC.index("SPECIALIST-FIRST: tools filtered")
    window = _SRC[max(0, fidx - 1600):fidx + 400]
    assert "_suppressed_tool_names" in window


# ── late-inject gate (extends the b0dda32 gate, doesn't duplicate) ──


def test_late_inject_gate_applies_filter() -> None:
    """Mutating tools recalled from history must stay suppressed under
    the flag — the late-inject door applies the same filter (incl. the
    fallback exemption, so a sanctioned browser fallback re-enters)."""
    gate = re.search(
        r"_specialist_first\s*\n?\s*"
        r"and not _specialist_first_filter_pass\(\s*"
        r"tc\.name,\s*_specialist_first_exempt,?\s*\)",
        _SRC,
    )
    assert gate is not None, (
        "late-inject path must refuse non-(meta|readonly|exempt) tools "
        "under LAZYCLAW_SPECIALIST_FIRST_BRAIN"
    )


def test_late_inject_gate_blocks_before_schema_lookup() -> None:
    gate_pos = _SRC.find("and not _specialist_first_filter_pass(")
    assert gate_pos != -1
    inject_pos = _SRC.find("get_tool_schema(tc.name)", gate_pos)
    assert inject_pos != -1
    between = _SRC[gate_pos:inject_pos]
    assert "_resurrect_blocked.append(tc.name)" in between
    assert "continue" in between


def test_thin_router_late_inject_gate_untouched() -> None:
    """The existing b0dda32 thin-router gate must remain."""
    gate = re.search(
        r"_thin_router_capped\s*\n?\s*and tc\.name not in _META_TOOLS",
        _SRC,
    )
    assert gate is not None


# ── AUTO-PROMOTE exclusion ──────────────────────────────────────────


def test_auto_promote_skipped_under_specialist_first() -> None:
    """The AUTO-PROMOTE trigger must not fire when _specialist_first is
    on (same exclusion as _thin_router); the existing _thin_router
    exclusion stays."""
    idx = _SRC.index("_promoted_to_bg = True")
    head = _SRC.rindex("if (", 0, idx)
    condition = _SRC[head:idx]
    assert "not _thin_router" in condition
    assert "not _specialist_first" in condition, (
        "AUTO-PROMOTE must be skipped under specialist-first"
    )


def test_thin_router_cap_failsafe_untouched() -> None:
    """The cap-aware responsiveness failsafe must keep working."""
    assert "THIN-ROUTER failsafe: brain refused to delegate" in _SRC


# ── stall failsafe (specialist-first alone, THIN_ROUTER off) ────────


def test_sf_stall_anchor_initialized_per_turn() -> None:
    """SPECIALIST_FIRST=1 + THIN_ROUTER=0 skips AUTO-PROMOTE and never
    engages the cap, so it needs its own stall anchor to arm the
    existing cap-aware failsafe — else a brain that pages read-only
    tools forever holds the foreground lane to max_iterations."""
    assert "_sf_stall_iter: int | None = None" in _SRC


def test_sf_stall_anchor_armed_on_first_inline_mutation() -> None:
    """Anchor at the top of the iteration after the first inline MUTATION.

    (2026-06-23 sheet-edit fix) Read-only inspections no longer arm the
    anchor — only a genuine mutation does — so a read-then-write flow
    (``read_sheet`` → ``set_cells``) stays inline. The condition keys on
    ``_is_inline_mutation``, not the old bare ``n not in _META_TOOLS`` which
    wrongly counted reads.
    """
    idx = _SRC.index("_sf_stall_iter = iteration")
    head = _SRC.rindex("if (", 0, idx)
    cond = _SRC[head:idx]
    assert "_specialist_first" in cond
    assert "_sf_stall_iter is None" in cond
    assert "any(_is_inline_mutation(n) for n in _called_tool_names)" in cond


def test_failsafe_arms_under_specialist_first_alone() -> None:
    """The existing cap-aware failsafe must also fire on the
    specialist-first stall anchor (same mechanics: one grace iteration,
    only when no dispatch tool was called; a final text answer exits
    the loop at `if not response.tool_calls:` before this block, so it
    only fires on a genuine stall)."""
    fidx = _SRC.index("THIN-ROUTER failsafe: brain refused to delegate")
    head = _SRC.rindex("if (", 0, fidx)
    cond = _SRC[head:fidx]
    assert "_cap_stalled or _sf_stalled" in cond
    for d in ("delegate", "dispatch_subagents", "run_background"):
        assert d in cond, d
    # The two stall predicates mirror each other's shape.
    sidx = _SRC.index("_sf_stalled = (")
    spred = _SRC[sidx:sidx + 220]
    assert "_specialist_first" in spred
    assert "_sf_stall_iter is not None" in spred
    assert "iteration > _sf_stall_iter" in spred
    cidx = _SRC.index("_cap_stalled = (")
    cpred = _SRC[cidx:cidx + 220]
    assert "_thin_router" in cpred
    assert "_thin_router_capped" in cpred
    assert "_cap_iter is not None" in cpred
    assert "iteration > _cap_iter" in cpred


def test_failsafe_auto_submits_to_task_runner() -> None:
    """Stall → auto-submit the original message to task_runner (chat
    stays responsive), same as the thin-router cap path."""
    fidx = _SRC.index("THIN-ROUTER failsafe: brain refused to delegate")
    region = _SRC[fidx:fidx + 2400]
    assert "._task_runner.submit(" in region
    assert "Continuing in background" in region


# ── router guidance line (exactly once per turn) ────────────────────


def test_guidance_constant_defined() -> None:
    assert "_SPECIALIST_FIRST_GUIDANCE" in _SRC
    guidance = agent_mod._SPECIALIST_FIRST_GUIDANCE
    assert "delegate" in guidance.lower()
    assert "router" in guidance.lower()


def test_guidance_injected_exactly_once() -> None:
    """One short system-context line at turn start — appended exactly
    once (single usage site, before the iteration loop)."""
    assert _SRC.count("content=_SPECIALIST_FIRST_GUIDANCE") == 1
    inject_idx = _SRC.index("content=_SPECIALIST_FIRST_GUIDANCE")
    loop_idx = _SRC.index("for iteration in range(max_iterations)")
    assert inject_idx < loop_idx, (
        "guidance must be injected at turn start, before the loop "
        "(so it fires once per turn, not once per iteration)"
    )
    # Gated on the flag.
    head = _SRC.rindex("_specialist_first", 0, inject_idx)
    assert inject_idx - head < 800
