"""Timeout & budget hierarchy — the guard against budget-math loops.

The 2026-08-09 → 08-14 "stuck loop" week was budget arithmetic, not agent
logic. Every one of those incidents was the SAME shape: a parent budget
expired while its child was still legitimately working, so the parent
killed the child, reported a timeout the child never observed, and the
brain retried the whole thing in a fresh generation — three generations,
zero results (2026-08-14 18:31-18:40).

THE INVARIANT: **a child budget must be strictly smaller than its
parent's.** If child >= parent, the parent dies first. It then orphans an
in-flight child (whose work is discarded), and it attributes the failure
to the wrong layer — which is why these bugs read as "the agent is stuck"
instead of "the numbers are wrong".

This file imports the REAL constants (never re-declares them) so a future
edit to any single number has to come here and re-justify the chain.

The chain, innermost → outermost:

    browser per-tool-call cap  180s  runtime/tool_executor.py
      < sync browser floor     480s  skills/builtin/agent_tool.py
        <= sync dispatch max   600s  skills/builtin/agent_tool.py
          <= background budget 600s  skills/builtin/agent_tool.py
            < executor ceiling 660s  AgentDispatchSkill.timeout
"""

from __future__ import annotations

from lazyclaw.runtime.task_runner import DEFAULT_TIMEOUT as TASK_RUNNER_DEFAULT
from lazyclaw.runtime.tool_executor import (
    DEFAULT_TOOL_TIMEOUT,
    PER_TOOL_TIMEOUTS,
    resolve_tool_timeout,
)
from lazyclaw.skills.builtin.agent_tool import (
    AgentDispatchSkill,
    _BG_TIMEOUT_S,
    _BROWSER_SYNC_TIMEOUT_FLOOR_S,
    _DEFAULT_TIMEOUT_S,
    _MAX_TIMEOUT_S,
    _MIN_TIMEOUT_S,
)

BROWSER_CALL_CAP = PER_TOOL_TIMEOUTS["browser"]


# ── The pinned values ───────────────────────────────────────────────────

def test_browser_per_call_cap_is_180s():
    """One Cloudflare-challenged navigation through the host Brave routinely
    exceeds the flat 60s default; at 60s the action died mid-navigation and
    the brain retried in background forever (2026-08-14)."""
    assert BROWSER_CALL_CAP == 180
    assert DEFAULT_TOOL_TIMEOUT == 60, (
        "the flat default stays 60s — the browser cap is an override, not a "
        "blanket raise; raising it globally would let every stuck MCP call "
        "hold a lane 3x longer"
    )


def test_dispatch_budgets_are_pinned():
    assert _DEFAULT_TIMEOUT_S == 120
    assert _BROWSER_SYNC_TIMEOUT_FLOOR_S == 480
    assert _MAX_TIMEOUT_S == 600
    assert _BG_TIMEOUT_S == 600
    assert _MIN_TIMEOUT_S == 10


def test_task_runner_default_is_documented():
    """TaskRunner's own default is 300s — SHORTER than several sync budgets,
    which is why the agent tool passes ``timeout=_BG_TIMEOUT_S`` explicitly
    on every background submit rather than inheriting it. Background exists
    FOR slow work; inheriting 300s made bg dispatch self-defeating. If this
    default ever changes, re-check that the explicit pass-through in
    ``AgentDispatchSkill._execute_background`` is still doing the work."""
    assert TASK_RUNNER_DEFAULT == 300
    assert TASK_RUNNER_DEFAULT < _BG_TIMEOUT_S, (
        "background agents must NOT inherit the shorter TaskRunner default — "
        "agent_tool.py overrides it explicitly; this asserts the override is "
        "still load-bearing"
    )


# ── The nesting invariants ──────────────────────────────────────────────

def test_browser_call_cap_nests_inside_browser_sync_floor():
    assert BROWSER_CALL_CAP < _BROWSER_SYNC_TIMEOUT_FLOOR_S, (
        f"browser per-call cap ({BROWSER_CALL_CAP}s) must be < the sync "
        f"browser specialist floor ({_BROWSER_SYNC_TIMEOUT_FLOOR_S}s). WHY: "
        "the specialist is the PARENT of every browser action it makes. If "
        "the per-call cap were >= the floor, the specialist would expire "
        "while its first navigation was still in flight — the browser action "
        "is orphaned mid-page and the failure is misattributed to dispatch."
    )


def test_browser_sync_floor_nests_inside_sync_max():
    assert _BROWSER_SYNC_TIMEOUT_FLOOR_S <= _MAX_TIMEOUT_S, (
        f"browser sync floor ({_BROWSER_SYNC_TIMEOUT_FLOOR_S}s) must be <= "
        f"the sync dispatch ceiling ({_MAX_TIMEOUT_S}s). WHY: the floor is "
        "applied by raising the caller's requested timeout — a floor above "
        "the ceiling would be silently unreachable, so browser dispatches "
        "would keep dying at whatever the LLM happened to request."
    )


def test_sync_max_nests_inside_background_budget():
    assert _MAX_TIMEOUT_S <= _BG_TIMEOUT_S, (
        f"sync dispatch ceiling ({_MAX_TIMEOUT_S}s) must be <= the "
        f"background budget ({_BG_TIMEOUT_S}s). WHY: background dispatch is "
        "the escape hatch for work too slow to run inline. A background "
        "budget shorter than the sync ceiling makes the escape hatch "
        "STRICTER than the thing it rescues — the exact self-defeating "
        "inversion that shipped when bg inherited TaskRunner's 300s."
    )


def test_sync_default_nests_inside_background_budget():
    assert _DEFAULT_TIMEOUT_S < _BG_TIMEOUT_S, (
        f"sync default ({_DEFAULT_TIMEOUT_S}s) must be < the background "
        f"budget ({_BG_TIMEOUT_S}s). WHY: 'this is taking too long, push it "
        "to background' must buy the task MORE time, never less."
    )


def test_dispatch_skill_ceiling_exceeds_its_own_inner_budget():
    """``AgentDispatchSkill.timeout`` is the ToolExecutor-level ceiling around
    a dispatch whose REAL budget is the inner per-call ``wait_for``. It must
    exceed ``_MAX_TIMEOUT_S`` or the executor (the parent) kills a dispatch
    that is still inside its own declared budget — a timeout nobody can
    explain from the child's logs."""
    assert AgentDispatchSkill.timeout > _MAX_TIMEOUT_S
    assert AgentDispatchSkill.timeout > _BG_TIMEOUT_S


def test_min_timeout_below_default():
    assert _MIN_TIMEOUT_S < _DEFAULT_TIMEOUT_S


def test_whole_chain_is_monotonic():
    """One assertion for the entire ladder — the table a reviewer reads."""
    chain = [
        ("browser per-call cap", BROWSER_CALL_CAP),
        ("browser sync floor", _BROWSER_SYNC_TIMEOUT_FLOOR_S),
        ("sync dispatch max", _MAX_TIMEOUT_S),
        ("background budget", _BG_TIMEOUT_S),
        ("dispatch executor ceiling", AgentDispatchSkill.timeout),
    ]
    for (child_name, child), (parent_name, parent) in zip(chain, chain[1:]):
        assert child <= parent, (
            f"{child_name} ({child}s) must nest inside {parent_name} "
            f"({parent}s) — a child budget larger than its parent means the "
            "parent expires first and orphans the child."
        )
    assert chain[0][1] < chain[-1][1]


# ── The override mechanism itself ───────────────────────────────────────

class _Skill:
    """Stand-in for a registered skill with no declared timeout."""


class _SkillWithTimeout:
    timeout = 999


def test_resolver_prefers_declared_skill_timeout():
    """A skill that declares its own budget wins over the table and the
    default — AgentDispatchSkill relies on this for its 660s ceiling."""
    assert resolve_tool_timeout(_SkillWithTimeout(), "browser", 60) == 999


def test_resolver_applies_per_tool_table():
    assert resolve_tool_timeout(_Skill(), "browser", DEFAULT_TOOL_TIMEOUT) == 180


def test_resolver_leaves_every_other_tool_unchanged():
    for name in ("read_sheet", "send_email", "recall_memories", "web_search"):
        assert resolve_tool_timeout(_Skill(), name, DEFAULT_TOOL_TIMEOUT) == 60, (
            f"{name} must keep the 60s default — the override table is an "
            "escape hatch for tools with genuinely long tail latency"
        )


def test_override_table_stays_small():
    """A per-skill config surface here would re-create the budget sprawl this
    file exists to prevent. Any addition must come with a nesting proof.

    Nesting proofs on file:
      browser (180s): one Cloudflare navigation exceeds 60s; 180 < 480 floor.
      ask_brain (320s): the tool legitimately WAITS — brain consult (~60s
        worst case) + 240s user-question checkpoint (QUESTION_TIMEOUT_SECONDS
        in skills/builtin/ask_brain.py). First live use (2026-08-16 09:55)
        proved the 60s default kills it mid-wait. 240 < 320 < 480 floor.
    """
    assert set(PER_TOOL_TIMEOUTS) == {"browser", "ask_brain"}
    for name, value in PER_TOOL_TIMEOUTS.items():
        assert value < _BROWSER_SYNC_TIMEOUT_FLOOR_S, (
            f"per-tool cap for {name} ({value}s) must stay under the "
            f"innermost dispatch budget ({_BROWSER_SYNC_TIMEOUT_FLOOR_S}s)"
        )

    # The inner checkpoint wait must finish BEFORE the executor cap, or the
    # executor kills ask_brain while the user is still typing an answer.
    from lazyclaw.skills.builtin.ask_brain import QUESTION_TIMEOUT_SECONDS

    assert QUESTION_TIMEOUT_SECONDS < PER_TOOL_TIMEOUTS["ask_brain"], (
        "ask_brain's user-question wait must be strictly inside its own "
        "executor cap (child < parent, always)"
    )


# ── Upwork MCP tools under the whole-tool lock (2026-08-21) ───────────
# The mcp-upwork @serialized lock (2026-08-21) made each tool call's
# navigate+scrape atomic — correct — but a QUEUED call now waits for the
# holder's slow Cloudflare navigation and then hit the 60s executor
# default: 7 timeouts on day one vs 0 the two days before. Upwork tools
# drive the same host Brave as `browser`, so they get the same 180s
# (nesting rule intact: 180 < 480 sync-browser floor < 600 ceiling).


def test_upwork_mcp_tools_get_the_browser_budget():
    uuid_name = (
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_messages"
    )
    assert resolve_tool_timeout(_Skill(), uuid_name, DEFAULT_TOOL_TIMEOUT) == 180


def test_unrelated_mcp_tools_keep_the_default():
    uuid_name = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_email_read"
    assert (
        resolve_tool_timeout(_Skill(), uuid_name, DEFAULT_TOOL_TIMEOUT)
        == DEFAULT_TOOL_TIMEOUT
    )


def test_explicit_skill_timeout_still_wins_for_mcp_names():
    uuid_name = (
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_messages"
    )
    assert resolve_tool_timeout(_SkillWithTimeout(), uuid_name, 60) == 999
