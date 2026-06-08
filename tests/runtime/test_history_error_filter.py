"""Stale-error history filter (2026-06-03).

A failed tool result from an earlier turn (e.g. ``draft_freelance_proposal``
hitting "credit balance too low") must NOT be replayed verbatim into the
brain's context — otherwise the brain reads it as a LIVE blocker and refuses to
re-attempt even after the underlying cause is fixed. ``_filter_error_messages``
drops assistant error dumps and neutralizes stale ``tool`` error results while
preserving tool_use↔tool_result pairing.
"""

from __future__ import annotations

from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.runtime.agent import (
    _STALE_TOOL_ERROR_MARKER,
    _filter_error_messages,
)

_CREDIT_ERR = (
    "Could not draft proposal: Error code: 400 - {'type': 'error', 'error': "
    "{'type': 'invalid_request_error', 'message': 'Your credit balance is too "
    "low to access the Anthropic API. Please go to Plans & Billing to upgrade "
    "or purchase credits.'}, 'request_id': 'req_011CbgSMgSgBV5JeQhSBHgLp'}"
)


def test_stale_tool_credit_error_is_neutralized_not_dropped():
    history = [
        LLMMessage(role="user", content="apply 1"),
        LLMMessage(
            role="assistant", content="", tool_calls=[],  # the tool_use turn
        ),
        LLMMessage(role="tool", content=_CREDIT_ERR, tool_call_id="call_1"),
    ]
    out = _filter_error_messages(history)
    # Message count preserved (pairing intact) — the tool msg is kept.
    assert len(out) == 3
    tool_msg = out[-1]
    assert tool_msg.role == "tool"
    assert tool_msg.tool_call_id == "call_1"  # pairing preserved
    assert tool_msg.content == _STALE_TOOL_ERROR_MARKER
    assert "credit balance" not in tool_msg.content
    assert "req_011" not in tool_msg.content


def test_tool_error_by_status_code_is_neutralized():
    history = [
        LLMMessage(role="tool", content="Error code: 503 - upstream down", tool_call_id="c2"),
    ]
    out = _filter_error_messages(history)
    assert out[0].content == _STALE_TOOL_ERROR_MARKER


def test_assistant_error_dump_is_dropped():
    history = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="Sorry, an error occurred (Error code: 400)"),
        LLMMessage(role="assistant", content="real answer"),
    ]
    out = _filter_error_messages(history)
    contents = [m.content for m in out]
    assert "real answer" in contents
    assert all("error occurred" not in (c or "") for c in contents)


def test_assistant_blocker_recitation_is_dropped():
    """The brain's OWN reply re-citing the credit blocker is also poison."""
    history = [
        LLMMessage(
            role="assistant",
            content="🔴 Blocker still active: credit balance too low — top up at Plans & Billing first.",
        ),
    ]
    assert _filter_error_messages(history) == []


def test_benign_tool_result_is_preserved():
    """A non-provider tool error (e.g. not-found) is real context — keep it."""
    history = [
        LLMMessage(role="tool", content="No file matched that path.", tool_call_id="c3"),
        LLMMessage(role="tool", content='{"unread": 2, "rooms": []}', tool_call_id="c4"),
    ]
    out = _filter_error_messages(history)
    assert [m.content for m in out] == [
        "No file matched that path.",
        '{"unread": 2, "rooms": []}',
    ]


def test_normal_conversation_untouched():
    history = [
        LLMMessage(role="user", content="what's the weather"),
        LLMMessage(role="assistant", content="It's sunny."),
    ]
    out = _filter_error_messages(history)
    assert [(m.role, m.content) for m in out] == [
        ("user", "what's the weather"),
        ("assistant", "It's sunny."),
    ]
