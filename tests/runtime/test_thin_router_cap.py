"""Phase 2 — thin-router 1-inline-action cap (flag-gated, default off).

Behind LAZYCLAW_THIN_ROUTER the brain may make at most one inline non-meta
tool call per turn; after that its tools narrow to META-only so it must
delegate. AUTO-PROMOTE is skipped under the flag (the cap replaces it).
"""

from __future__ import annotations

from pathlib import Path

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def test_meta_tools_constant_defined() -> None:
    assert "_META_TOOLS" in _AGENT_SRC, "Phase 2 needs a _META_TOOLS set"
    # must include the dispatch + base meta tools
    for name in ("delegate", "dispatch_subagents", "run_background",
                 "search_tools", "recall_memories"):
        assert f'"{name}"' in _AGENT_SRC


def test_thin_router_flag_read() -> None:
    assert "LAZYCLAW_THIN_ROUTER" in _AGENT_SRC, (
        "Phase 2 must read the LAZYCLAW_THIN_ROUTER env flag"
    )
    assert "_thin_router" in _AGENT_SRC


def test_thin_router_narrows_to_meta_only() -> None:
    """There must be a flag-gated narrowing block keyed on _thin_router that
    restricts tools to _META_TOOLS after an inline domain call."""
    assert "THIN-ROUTER" in _AGENT_SRC, "expected a THIN-ROUTER narrowing block"
    idx = _AGENT_SRC.index("THIN-ROUTER")
    # Window widened 2026-06-24: the work-call budget trigger + dispatch-only
    # narrowing added lines between the block header and the narrow/suppress
    # statements.
    window = _AGENT_SRC[idx:idx + 3200]
    assert "_thin_router" in window
    assert "_META_TOOLS" in window
    assert "_suppressed_tool_names" in window, (
        "narrowing must suppress non-meta names like the AUTO-PROMOTE path"
    )


def test_auto_promote_skipped_under_thin_router() -> None:
    """The AUTO-PROMOTE trigger must not fire when _thin_router is on."""
    idx = _AGENT_SRC.index("_promoted_to_bg = True")
    head = _AGENT_SRC.rindex("if (", 0, idx)
    condition = _AGENT_SRC[head:idx]
    assert "not _thin_router" in condition, (
        "AUTO-PROMOTE must be skipped under thin-router (cap replaces it)"
    )
