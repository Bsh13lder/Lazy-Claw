"""The URL-mined fallback row must describe itself honestly.

2026-07-26 incident: with the user's tab parked on a conversation view,
the rooms sidebar never rendered. All 13 ``get_messages`` calls logged
"0 room elements matched" and fell into the URL-mining fallback, which
returned a row carrying room_id / room_url / contact_name / source and
NOTHING to say "this is not an inbox listing".

Because ``conversations`` was then non-empty, execution returned before
``empty_or_blocked_result(page)``, so the consumer got a plausible
listing with no listing content and no error marker to branch on. It
reported "No Upwork conversations found" while a direct
``get_conversation_messages`` on the same room returned 20 real bubbles.

The ``status`` field added here is what lets the consumer route to
``upwork_get_conversation`` instead of guessing.
"""

from __future__ import annotations

from upwork_mcp.tools.messages import build_url_fallback_row

ROOM_ID = "room_e09f7619c1f68dd8b44544c5c4433c97"
ROOM_URL = f"https://www.upwork.com/ab/messages/rooms/{ROOM_ID}"


def test_row_declares_sidebar_unavailable():
    row = build_url_fallback_row(ROOM_ID, ROOM_URL, "")
    assert row["status"] == "sidebar_unavailable"


def test_row_carries_actionable_hint():
    row = build_url_fallback_row(ROOM_ID, ROOM_URL, "")
    hint = row["hint"]
    assert hint.strip(), "hint must not be empty"
    # The hint has to name the recovery tool — that is its whole job.
    assert "upwork_get_conversation" in hint


def test_row_preserves_the_keys_consumers_key_off():
    # lazyclaw's _normalize_inbox coerces a bare row to a one-element
    # list based on room_id / contact_name, and _blocked_diagnosis
    # decides "readable vs blocked" on room_id / room_url. Renaming or
    # dropping any of these silently reintroduces the 11-minute loop.
    row = build_url_fallback_row(ROOM_ID, ROOM_URL, "James Blue")
    assert row["room_id"] == ROOM_ID
    assert row["room_url"] == ROOM_URL
    assert row["contact_name"] == "James Blue"
    assert row["source"] == "page_url_fallback"


def test_empty_contact_name_is_preserved_not_dropped():
    # The incident row had contact_name == "" — falsy but real.
    row = build_url_fallback_row(ROOM_ID, ROOM_URL, "")
    assert "contact_name" in row
    assert row["contact_name"] == ""


def test_status_matches_the_literal_the_consumer_accepts():
    # Cross-repo contract check. lazyclaw's _blocked_diagnosis accepts
    # exactly this string; a typo on either side is a silent regression.
    row = build_url_fallback_row(ROOM_ID, ROOM_URL, "")
    assert row["status"] == "sidebar_unavailable"


def test_row_is_a_fresh_object_each_call():
    # House rule: no shared mutable state handed to callers.
    a = build_url_fallback_row(ROOM_ID, ROOM_URL, "")
    b = build_url_fallback_row(ROOM_ID, ROOM_URL, "")
    assert a is not b
    a["room_id"] = "mutated"
    assert b["room_id"] == ROOM_ID
