"""Tests for lazyclaw/runtime/mcp_tool_reinject.py.

Covers the post-execution hook that adds newly-registered MCP tools to
the per-turn ``tools`` list after a ``connect_mcp_server`` or
``install_mcp_server`` call succeeds.
"""
from __future__ import annotations

import pytest

from lazyclaw.runtime.mcp_tool_reinject import (
    reinject_mcp_tools,
    snapshot_mcp_tool_names,
)


# ── Fakes ────────────────────────────────────────────────────────────


def _tool(name: str) -> dict:
    """Build a minimal tool-schema dict shaped like the registry returns."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"stub for {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class FakeRegistry:
    def __init__(self, mcp_tools: list[dict]) -> None:
        self._mcp_tools = mcp_tools

    def list_mcp_tools(self) -> list[dict]:
        return list(self._mcp_tools)

    def get_tool_schema(self, name: str) -> dict | None:
        for t in self._mcp_tools:
            if (t.get("function") or {}).get("name") == name:
                return t
        return None


# ── Happy path ───────────────────────────────────────────────────────


def test_injects_new_mcp_tools_into_tools_list() -> None:
    reg = FakeRegistry([
        _tool("mcp_abc_claude_code"),
        _tool("mcp_abc_other_tool"),
    ])
    tools: list[dict] = []
    injected = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    assert sorted(injected) == ["mcp_abc_claude_code", "mcp_abc_other_tool"]
    # Verify tools list was mutated in place
    assert {(t.get("function") or {}).get("name") for t in tools} == set(injected)


def test_skips_tools_already_present_in_tools_list() -> None:
    reg = FakeRegistry([
        _tool("mcp_abc_claude_code"),
        _tool("mcp_abc_existing"),
    ])
    tools = [_tool("mcp_abc_existing")]
    injected = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    # Only the previously-unknown tool should be injected
    assert injected == ["mcp_abc_claude_code"]
    # `tools` should have both
    names = [(t.get("function") or {}).get("name") for t in tools]
    assert names == ["mcp_abc_existing", "mcp_abc_claude_code"]


def test_skips_suppressed_tools() -> None:
    # AUTO-PROMOTE narrowing path adds names to suppressed_tool_names —
    # reinjection MUST respect that (would otherwise resurrect tools the
    # runtime intentionally hid for force-dispatch).
    reg = FakeRegistry([
        _tool("mcp_abc_claude_code"),
        _tool("mcp_abc_should_be_hidden"),
    ])
    tools: list[dict] = []
    injected = reinject_mcp_tools(
        reg, tools, suppressed_tool_names={"mcp_abc_should_be_hidden"},
    )
    assert injected == ["mcp_abc_claude_code"]
    names = [(t.get("function") or {}).get("name") for t in tools]
    assert "mcp_abc_should_be_hidden" not in names


def test_returns_empty_when_no_mcp_tools() -> None:
    reg = FakeRegistry([])
    tools: list[dict] = []
    injected = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    assert injected == []
    assert tools == []


def test_returns_empty_when_registry_is_none() -> None:
    tools: list[dict] = []
    injected = reinject_mcp_tools(
        None, tools, suppressed_tool_names=set(),
    )
    assert injected == []
    assert tools == []


def test_accepts_none_for_suppressed_arg() -> None:
    reg = FakeRegistry([_tool("mcp_abc_claude_code")])
    tools: list[dict] = []
    injected = reinject_mcp_tools(reg, tools, suppressed_tool_names=None)
    assert injected == ["mcp_abc_claude_code"]


def test_swallows_registry_exceptions() -> None:
    class BrokenRegistry:
        def list_mcp_tools(self):
            raise RuntimeError("registry on fire")

        def get_tool_schema(self, name):  # pragma: no cover
            return None

    tools: list[dict] = []
    injected = reinject_mcp_tools(BrokenRegistry(), tools, suppressed_tool_names=set())
    assert injected == []
    assert tools == []


def test_skips_tool_with_no_schema() -> None:
    # Registry advertises a tool name but get_tool_schema returns None
    # (e.g. lazy-registered stub never fully resolved). MUST skip it
    # silently instead of injecting a None schema and breaking the LLM.
    class FlakeyRegistry:
        def list_mcp_tools(self):
            return [_tool("mcp_abc_real"), _tool("mcp_abc_phantom")]

        def get_tool_schema(self, name):
            if name == "mcp_abc_phantom":
                return None
            return _tool(name)

    tools: list[dict] = []
    injected = reinject_mcp_tools(
        FlakeyRegistry(), tools, suppressed_tool_names=set(),
    )
    assert injected == ["mcp_abc_real"]


def test_drops_malformed_existing_entries() -> None:
    # Defensive: existing tools list might have entries without
    # function/name (e.g. provider-specific shapes). MUST NOT crash.
    reg = FakeRegistry([_tool("mcp_abc_claude_code")])
    tools = [{"function": None}, {"weird": "shape"}]
    injected = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    assert injected == ["mcp_abc_claude_code"]


def test_idempotent_across_two_calls() -> None:
    # Second call with the same registry must not re-inject anything.
    reg = FakeRegistry([_tool("mcp_abc_claude_code")])
    tools: list[dict] = []
    first = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    second = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    assert first == ["mcp_abc_claude_code"]
    assert second == []
    assert len(tools) == 1


# ── Fix I — pre_connect_tool_names delta filter ─────────────────────


def test_snapshot_captures_registry_state() -> None:
    reg = FakeRegistry([
        _tool("mcp_abc_one"),
        _tool("mcp_abc_two"),
        _tool("mcp_xyz_three"),
    ])
    snap = snapshot_mcp_tool_names(reg)
    assert snap == {"mcp_abc_one", "mcp_abc_two", "mcp_xyz_three"}


def test_snapshot_returns_empty_on_none_registry() -> None:
    assert snapshot_mcp_tool_names(None) == set()


def test_delta_filter_only_injects_newly_registered() -> None:
    # Live bug 2026-05-14 13:48: connecting one MCP added 127 tools
    # because the helper saw all already-registered MCPs as "missing"
    # from the curated per-turn `tools`. Fix I: with the pre_connect
    # snapshot, only tools NEW since the snapshot are eligible.
    pre = {"mcp_existing_a", "mcp_existing_b", "mcp_other_z"}
    reg = FakeRegistry([
        _tool("mcp_existing_a"),
        _tool("mcp_existing_b"),
        _tool("mcp_other_z"),
        _tool("mcp_newly_connected_x"),
        _tool("mcp_newly_connected_y"),
    ])
    # Per-turn tools intentionally only has 1 of the pre-existing tools
    # (channel-keyword router curated it).
    tools = [_tool("mcp_existing_a")]
    injected = reinject_mcp_tools(
        reg, tools,
        suppressed_tool_names=set(),
        pre_connect_tool_names=pre,
    )
    # Only the two genuinely new tools should be injected — not the
    # other pre-existing ones (mcp_existing_b, mcp_other_z) even though
    # they're in registry but not in tools.
    assert sorted(injected) == [
        "mcp_newly_connected_x", "mcp_newly_connected_y",
    ]


def test_no_delta_filter_keeps_old_behavior() -> None:
    # When pre_connect_tool_names is None, the helper falls back to the
    # original "anything in registry not in tools" behavior. Backward-
    # compatible for any caller that doesn't snapshot.
    reg = FakeRegistry([
        _tool("mcp_a"),
        _tool("mcp_b"),
    ])
    tools: list[dict] = []
    injected = reinject_mcp_tools(reg, tools, suppressed_tool_names=set())
    assert sorted(injected) == ["mcp_a", "mcp_b"]


def test_delta_filter_with_empty_snapshot_treats_all_as_new() -> None:
    reg = FakeRegistry([
        _tool("mcp_a"),
        _tool("mcp_b"),
    ])
    tools: list[dict] = []
    injected = reinject_mcp_tools(
        reg, tools,
        suppressed_tool_names=set(),
        pre_connect_tool_names=set(),
    )
    assert sorted(injected) == ["mcp_a", "mcp_b"]


def test_delta_filter_respects_suppressed_set() -> None:
    # Suppressed names must still be skipped even if they're in the
    # new-since-snapshot delta. AUTO-PROMOTE narrowing depends on this.
    reg = FakeRegistry([
        _tool("mcp_new_safe"),
        _tool("mcp_new_blocked"),
    ])
    tools: list[dict] = []
    injected = reinject_mcp_tools(
        reg, tools,
        suppressed_tool_names={"mcp_new_blocked"},
        pre_connect_tool_names=set(),
    )
    assert injected == ["mcp_new_safe"]


def test_delta_filter_respects_existing_tools() -> None:
    # If a tool is already in `tools`, don't add it again even when
    # it's flagged "new" by the delta. Existing-in-tools wins.
    reg = FakeRegistry([
        _tool("mcp_already_in_tools"),
        _tool("mcp_truly_new"),
    ])
    tools = [_tool("mcp_already_in_tools")]
    injected = reinject_mcp_tools(
        reg, tools,
        suppressed_tool_names=set(),
        pre_connect_tool_names=set(),
    )
    assert injected == ["mcp_truly_new"]
