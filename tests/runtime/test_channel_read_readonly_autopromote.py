"""Channel-read tools must be recognized as read-only so a quick
"check my whatsapp" on the Web UI is answered INLINE instead of being
AUTO-PROMOTE'd to a background task whose result lands on Telegram.

2026-05-26 incident: a Web UI turn "Check my whatsapp..." called
`whatsapp_list_chats` + `whatsapp_read`, but `whatsapp_list_chats` was
not in `_CHANNEL_READ_TOOL_NAME_PATTERNS`, so `_only_readonly_so_far`
was False → AUTO-PROMOTE fired → background task → verbose Telegram push
(the "weird message"). The fix recognizes channel-listing reads.
"""

from __future__ import annotations

from lazyclaw.runtime import result_verifier as _rv
from lazyclaw.runtime.agent import (
    _CHANNEL_READ_TOOL_NAME_PATTERNS,
    _is_channel_read_tool_name,
)


def test_whatsapp_list_chats_recognized_as_channel_read() -> None:
    # The exact tool the brain calls for "check my whatsapp" — must be
    # recognized even with the mcp_<uuid>_ wrapper prefix.
    assert _is_channel_read_tool_name(
        "mcp_bab07595-9b21-441c-84f7-1486df4dbf38_whatsapp_list_chats"
    )
    assert _is_channel_read_tool_name("whatsapp_list_chats")


def test_existing_channel_reads_still_recognized() -> None:
    for name in (
        "mcp_x_whatsapp_read",
        "mcp_x_email_read",
        "mcp_x_instagram_read_dms",
        "upwork_last_conversation",
    ):
        assert _is_channel_read_tool_name(name), name


def test_instagram_notifications_recognized() -> None:
    assert _is_channel_read_tool_name("mcp_x_instagram_get_notifications")


def test_patterns_include_channel_listings() -> None:
    assert "whatsapp_list_chats" in _CHANNEL_READ_TOOL_NAME_PATTERNS
    assert "instagram_get_notifications" in _CHANNEL_READ_TOOL_NAME_PATTERNS


# ── classify() must be SKIPPED for channel reads ──────────────────────────
# result_verifier's failure-marker regexes (e.g. "timed out",
# "not authorized") match the *quoted human message* inside a conversation
# JSON and falsely stamp it `→ FAILED:`. agent.py guards the classify()
# call with _is_channel_read_tool_name so a quoted human message is never
# mistaken for a tool failure. These tests pin both halves of that guard.

_CHANNEL_READ_WITH_FAILURE_WORDS = (
    '[{"sender":"James","content":"The accept bot timed out '
    'last night, can you fix it?"}]'
)


def test_classify_would_falsely_fail_channel_read_without_guard() -> None:
    # Proves the danger: classify() on its own DOES flag a quoted human
    # "timed out" as a failure — which is exactly why the call site must
    # skip it for channel reads.
    status, reason = _rv.classify(
        "upwork_get_conversation", _CHANNEL_READ_WITH_FAILURE_WORDS,
    )
    assert status == "failed"
    assert reason == "timeout"


def test_channel_read_tool_is_gated_so_classify_is_skipped() -> None:
    # The guard the agent uses: when this returns True, classify() is
    # never called, so the quoted human message can't be stamped FAILED.
    assert _is_channel_read_tool_name("upwork_get_conversation")
    assert _is_channel_read_tool_name(
        "mcp_x_whatsapp_read"
    )


def test_non_channel_tool_still_classified_as_failed() -> None:
    # Non-channel tools are NOT gated, so genuine failures still get
    # stamped — the fix must not blanket-disable classify().
    assert not _is_channel_read_tool_name("run_command")
    status, reason = _rv.classify(
        "run_command", "Error: the request timed out",
    )
    assert status == "failed"
