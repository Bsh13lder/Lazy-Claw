"""Brain-side bare-suffix tool-name rescue + delegate-steering correction.

Production incident 2026-07-02: the brain called Upwork MCP tools under
wrong names — a bare name (``upwork_get_job_details``) and a STALE
``mcp_<old-uuid>_`` prefix leaked from another user's history. Every
existing validity check (``agent.py`` drop-check block,
``registry.get_tool_schema``) is byte-exact, so both calls were dropped as
hallucinations and the turn hit the 3-strikes hallucination cap and bailed.

These tests pin two pure helpers in ``lazyclaw/runtime/agent.py``:

1. ``_suffix_rescue_tool_calls`` — rewrites a hallucinated call to the ONE
   attached tool whose bare name matches, WITHOUT ever searching outside
   the tools attached this turn (that would bypass thin-router /
   specialist-first suppression).
2. ``_build_hallucination_correction`` — when the bad name's bare form
   matches a tool that IS registered on a connected MCP server but was
   simply not attached this turn, the correction message must steer the
   model to ``delegate`` instead of the useless ``search_tools`` hint.
"""

from __future__ import annotations

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.runtime.agent import (
    _suffix_rescue_tool_calls,
    _build_hallucination_correction,
)

REAL = "mcp_d6efb25b-a85a-4b78-ad73-6fec833fef72_upwork_get_job_details"
STALE = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_job_details"


def test_bare_name_rescued_to_attached_mcp_name():
    calls = [ToolCall(id="1", name="upwork_get_job_details", arguments={"url": "x"})]
    fixed, log = _suffix_rescue_tool_calls(calls, {REAL, "search_tools"})
    assert fixed[0].name == REAL
    assert fixed[0].arguments == {"url": "x"} and fixed[0].id == "1"
    assert log == [f"upwork_get_job_details → {REAL}"]


def test_stale_uuid_prefix_rescued_to_current_uuid():
    calls = [ToolCall(id="1", name=STALE, arguments={})]
    fixed, _ = _suffix_rescue_tool_calls(calls, {REAL})
    assert fixed[0].name == REAL


def test_ambiguous_suffix_not_rescued():
    other = "mcp_11111111-1111-1111-1111-111111111111_upwork_get_job_details"
    calls = [ToolCall(id="1", name="upwork_get_job_details", arguments={})]
    fixed, log = _suffix_rescue_tool_calls(calls, {REAL, other})
    assert fixed[0].name == "upwork_get_job_details" and log == []


def test_valid_and_invented_names_untouched():
    calls = [ToolCall(id="1", name=REAL, arguments={}),
             ToolCall(id="2", name="apply_job", arguments={})]
    fixed, log = _suffix_rescue_tool_calls(calls, {REAL})
    assert [c.name for c in fixed] == [REAL, "apply_job"] and log == []


def test_correction_steers_to_delegate_when_registered_but_not_attached():
    class FakeRegistry:
        def list_names_by_prefix(self, prefix):
            return [REAL] if prefix == "mcp_" else []
    msg = _build_hallucination_correction(
        "upwork_get_job_details", {"search_tools", "delegate"}, FakeRegistry(),
    )
    assert "delegate" in msg
    assert "search_tools('details')" not in msg
