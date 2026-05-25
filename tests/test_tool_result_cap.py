"""Tests for _cap_tool_result — the per-tool channel-read exception.

The bug being closed: the global ``_MAX_TOOL_RESULT_CHARS = 4000`` cap
was applied to EVERY tool result, including channel reads where the
whole conversation IS the load-bearing data. The 2026-05-24 incident:
brain's ``upwork_get_conversation`` returned a 20-bubble JSON (~10.9 KB)
which got chopped to 4 KB with the last 6598 chars replaced by a single
``[truncated N chars]`` marker — brain saw only the first 3 messages
and the user thought the brain was hallucinating "no new messages".

The fix: channel-read tools (``upwork_*``, ``whatsapp_read*``,
``email_read*``, ``instagram_read*``, ``telegram_get_messages``) get a
50 KB cap instead of 4 KB. Other tools keep the 4 KB default to avoid
context blowup on mega-array returns (50+ contacts, page snapshots).
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.agent import (
    _MAX_TOOL_RESULT_CHARS,
    _MAX_TOOL_RESULT_CHARS_CHANNEL_READ,
    _cap_tool_result,
    _is_channel_read_tool_name,
)


# ─── _is_channel_read_tool_name ──────────────────────────────────────────


@pytest.mark.parametrize("name,expected", [
    # Upwork — direct names + MCP-wrapped names
    ("upwork_get_conversation", True),
    ("upwork_get_messages", True),
    ("upwork_last_conversation", True),
    ("upwork_inbox_check", True),
    ("mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_conversation", True),
    ("mcp_abc_UPWORK_GET_MESSAGES", True),  # case-insensitive
    # WhatsApp
    ("whatsapp_read", True),
    ("whatsapp_get_messages", True),
    ("whatsapp_get_chat", True),
    # Email / Instagram / Telegram
    ("email_read", True),
    ("instagram_read_dms", True),
    ("instagram_get_dms", True),
    ("telegram_get_messages", True),
    # NOT channel reads — should NOT trigger the larger cap
    ("upwork_send_message", False),
    ("upwork_submit_proposal", False),
    ("lazybrain_recall_typed_memory", False),
    ("save_memory", False),
    ("search_tools", False),
    ("", False),
    (None, False),
])
def test_channel_read_tool_detection(name, expected):
    assert _is_channel_read_tool_name(name) is expected


# ─── _cap_tool_result ────────────────────────────────────────────────────


def test_no_cap_when_under_4k_default():
    """Short results pass through unchanged regardless of tool name."""
    short = "x" * 1000
    assert _cap_tool_result(short) == short
    assert _cap_tool_result(short, "any_tool") == short


def test_default_cap_truncates_at_4k():
    """Non-channel tools hit the 4 KB cap with the truncation marker."""
    long = "x" * 6000
    out = _cap_tool_result(long, "lazybrain_search_notes")
    assert len(out) < 6000
    assert out.startswith("x" * _MAX_TOOL_RESULT_CHARS)
    assert "[truncated 2000 chars]" in out


def test_channel_read_uses_50k_cap_not_4k():
    """The smoking gun — channel-read JSON of 10.9 KB used to get
    chopped to 4 KB. Now it passes through clean because 10.9 KB < 50 KB."""
    payload = "x" * 10_900  # exact size of the 2026-05-24 incident
    out = _cap_tool_result(payload, "upwork_get_conversation")
    assert out == payload  # not truncated
    assert "[truncated" not in out


def test_channel_read_with_mcp_wrapper_name_uses_50k_cap():
    """MCP bridges wrap tool names like
    ``mcp_<hash>_upwork_get_conversation``. The substring match must
    still recognize them — that's the name the brain actually sees."""
    payload = "x" * 12_000
    wrapped = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_conversation"
    out = _cap_tool_result(payload, wrapped)
    assert out == payload


def test_channel_read_still_truncates_at_50k():
    """An absurdly long conversation (>50 KB) still gets capped to
    keep cache budget bounded — just at the higher threshold."""
    huge = "x" * 60_000
    out = _cap_tool_result(huge, "upwork_get_conversation")
    assert len(out) < 60_000
    assert out.startswith("x" * _MAX_TOOL_RESULT_CHARS_CHANNEL_READ)
    assert "[truncated 10000 chars]" in out


def test_legacy_no_tool_name_falls_back_to_4k_default():
    """Backward compat: callers that don't pass tool_name see the
    original behavior (4 KB cap). No silent regression for older
    code paths that called _cap_tool_result(result) positionally."""
    long = "x" * 6000
    out = _cap_tool_result(long)  # no tool_name kwarg
    assert "[truncated 2000 chars]" in out


def test_empty_input_returns_empty():
    assert _cap_tool_result("") == ""
    assert _cap_tool_result("", "upwork_get_conversation") == ""


def test_send_message_uses_default_cap_not_channel_cap():
    """`upwork_send_message` is a WRITE tool — it doesn't return
    conversation data, just success/error JSON. Stays on the small
    cap so a misbehaving server can't blow up context."""
    payload = "x" * 6000
    out = _cap_tool_result(payload, "upwork_send_message")
    assert "[truncated 2000 chars]" in out


def test_truncation_marker_format_includes_remaining_count():
    """The `[truncated N chars]` marker tells the reader how much was
    dropped. Important for diagnostics — if the marker says
    `truncated 6598 chars`, you know the tool returned a lot more
    than what the brain got."""
    payload = "y" * 5000
    out = _cap_tool_result(payload, "any_non_channel_tool")
    assert out.endswith("[truncated 1000 chars]")
