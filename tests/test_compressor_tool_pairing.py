"""Validator must produce Anthropic-safe tool_use ↔ tool_result pairings.

Anthropic rejects with "tool call result does not follow tool call (2013)"
whenever a tool_use isn't immediately satisfied by its tool_result. The
compressor's validator is the last line of defence — these tests pin the
contract for the failure modes we've actually hit in production.
"""

from __future__ import annotations

import json

from lazyclaw.memory.compressor import _to_llm_messages


def _assistant(call_ids: list[str]) -> dict:
    return {
        "id": "a",
        "role": "assistant",
        "content": "",
        "tool_name": None,
        "metadata": json.dumps([
            {"id": cid, "name": "foo", "arguments": {}} for cid in call_ids
        ]),
        "has_tool_calls": True,
    }


def _tool(call_id: str) -> dict:
    return {
        "id": call_id,
        "role": "tool",
        "content": "ok",
        "tool_name": call_id,
        "metadata": None,
        "has_tool_calls": False,
    }


def _user(text: str = "hi") -> dict:
    return {
        "id": "u", "role": "user", "content": text,
        "tool_name": None, "metadata": None, "has_tool_calls": False,
    }


def _system(text: str = "guidance") -> dict:
    return {
        "id": "s", "role": "system", "content": text,
        "tool_name": None, "metadata": None, "has_tool_calls": False,
    }


def _pairs_ok(msgs) -> bool:
    """Every assistant.tool_calls id must have a matching tool message right after."""
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m.role == "assistant" and m.tool_calls:
            expected = [tc.id for tc in m.tool_calls]
            j = i + 1
            for cid in expected:
                # System messages between tool_use and tool_result are NOT
                # allowed by Anthropic — we should never emit one here.
                if j >= len(msgs) or msgs[j].role != "tool":
                    return False
                if msgs[j].tool_call_id != cid:
                    return False
                j += 1
            i = j
        else:
            i += 1
    return True


def test_orphan_tool_dropped():
    raw = [_user(), _tool("X"), _user("next")]
    out = _to_llm_messages(raw)
    assert all(m.role != "tool" for m in out)


def test_partial_tool_results_get_stubs():
    # assistant called A and B but only A's result was persisted
    raw = [_assistant(["A", "B"]), _tool("A"), _user("next")]
    out = _to_llm_messages(raw)
    assert _pairs_ok(out)
    # B should have been stubbed
    stub_ids = [m.tool_call_id for m in out if m.role == "tool"]
    assert "A" in stub_ids and "B" in stub_ids


def test_system_between_tool_results_does_not_orphan_them():
    # The exact production failure: system row written between tool A and tool B
    raw = [
        _assistant(["A", "B"]),
        _tool("A"),
        _system("recovery hint"),
        _tool("B"),
        _user("next"),
    ]
    out = _to_llm_messages(raw)
    tool_ids = [m.tool_call_id for m in out if m.role == "tool"]
    assert tool_ids == ["A", "B"]
    assert _pairs_ok(out)


def test_unsatisfied_tail_assistant_gets_stub():
    raw = [_assistant(["A"])]  # no tool result at all
    out = _to_llm_messages(raw)
    assert _pairs_ok(out)
