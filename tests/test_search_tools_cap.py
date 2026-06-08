"""Phase 3c — cap tool-DISCOVERY (`search_tools`) spam per assistant turn.

2026-06-08: the documents_specialist emitted ~19 distinct-query `search_tools`
calls in ONE turn (panic-search) while thrashing a Google-Sheet task. Distinct
queries survive the exact-args dedup, so they all ran — token burn + thrash.
`_dedup_tool_calls` now keeps only the first `_DISCOVERY_TOOLS_CAP` discovery
calls per turn. Covers brain AND worker (both go through this provider).
"""

from __future__ import annotations

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.llm.providers.claude_sdk_provider import (
    _DISCOVERY_TOOLS_CAP,
    _dedup_tool_calls,
)


def _st(i: int) -> ToolCall:
    return ToolCall(id=f"t{i}", name="search_tools", arguments={"query": f"q{i}"})


def test_search_tools_spam_capped() -> None:
    calls = [_st(i) for i in range(19)]
    out = _dedup_tool_calls(calls)
    kept = [c for c in out if c.name == "search_tools"]
    assert len(kept) == _DISCOVERY_TOOLS_CAP, (
        f"19 distinct search_tools should cap to {_DISCOVERY_TOOLS_CAP}, "
        f"got {len(kept)}"
    )
    # Order preserved — the FIRST N survive.
    assert [c.arguments["query"] for c in kept] == ["q0", "q1", "q2"]


def test_under_cap_all_survive() -> None:
    calls = [_st(0), _st(1)]
    out = _dedup_tool_calls(calls)
    assert len([c for c in out if c.name == "search_tools"]) == 2


def test_other_tools_unaffected_by_discovery_cap() -> None:
    # A non-discovery tool with distinct args is never capped by this pass.
    calls = [
        ToolCall(id=f"w{i}", name="web_search", arguments={"query": f"q{i}"})
        for i in range(6)
    ]
    out = _dedup_tool_calls(calls)
    assert len(out) == 6, "web_search distinct queries must still survive"


def test_discovery_cap_mixed_with_real_call() -> None:
    calls = [_st(0), _st(1), _st(2), _st(3), _st(4)]
    calls.append(
        ToolCall(id="g", name="google_run_task", arguments={"task_type": "x"})
    )
    out = _dedup_tool_calls(calls)
    names = [c.name for c in out]
    assert names.count("search_tools") == _DISCOVERY_TOOLS_CAP
    assert "google_run_task" in names, "the real action call must survive"
