"""Tests for lazyclaw/runtime/mcp_tool_reinject.py.

Covers the post-execution hook that adds newly-registered MCP tools to
the per-turn ``tools`` list after a ``connect_mcp_server`` or
``install_mcp_server`` call succeeds.
"""
from __future__ import annotations

import pytest

from lazyclaw.runtime.mcp_tool_reinject import reinject_mcp_tools


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
