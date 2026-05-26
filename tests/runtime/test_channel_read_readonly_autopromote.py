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
