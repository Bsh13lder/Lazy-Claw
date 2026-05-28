"""Tests for the paraphrase sanitizer that blocks the on-demand recall
leak path (2026-05-24).

Leak path being closed: when the brain calls
``lazybrain_recall_typed_memory("James Blue")`` or
``lazybrain_semantic_search("james upwork")``, daily-log /
session-log notes containing pre-formatted ``**James Blue (10:37 PM):**``
strings come back verbatim. Opus+cache then mimics those strings in
its reply as if they were live ``upwork_get_conversation`` quotes.

The sanitizer:
  1. Identifies paraphrase-class memory types (``session-log``,
     ``fact``, ``other``, NULL).
  2. Strips embedded sender-timestamp patterns
     (``**Sender (HH:MM):**``, ``> Sender (HH:MM):``, etc.) by
     replacing them with ``[paraphrased: Sender @ HH:MM]`` markers.
  3. Wraps the result in
     ``[CACHED PARAPHRASE … NOT a live channel quote]`` framing.

Authoritative-state types (``user`` / ``feedback`` / ``project`` /
``reference``) pass through unchanged.
"""

from __future__ import annotations

import pytest

from lazyclaw.lazybrain.paraphrase_sanitizer import (
    is_paraphrase_class,
    sanitize_recall_content,
    strip_sender_timestamp_patterns,
    wrap_paraphrase,
)


# ─── is_paraphrase_class ─────────────────────────────────────────────────


@pytest.mark.parametrize("mt,expected", [
    ("session-log", True),
    ("fact", False),    # fact is now authoritative — passes through unchanged
    ("other", True),
    (None, True),       # fail-closed: NULL → paraphrase
    ("", True),         # empty string → paraphrase
    ("unknown_value", True),  # unknown → fail-closed
    ("user", False),
    ("feedback", False),
    ("project", False),
    ("reference", False),
])
def test_is_paraphrase_class(mt, expected):
    assert is_paraphrase_class(mt) is expected


# ─── strip_sender_timestamp_patterns ─────────────────────────────────────


def test_strip_bold_block_pattern():
    """`**Sender (HH:MM):**` line-prefix gets replaced with marker."""
    text = "Header note\n**James Blue (10:37 PM):**\nthe content"
    out = strip_sender_timestamp_patterns(text)
    assert "**James Blue (10:37 PM):**" not in out
    assert "[paraphrased: James Blue @ 10:37 PM]" in out
    assert "the content" in out  # body preserved


def test_strip_bold_sender_plain_ts_pattern():
    """`**Sender** (HH:MM):` shape (bold sender, plain ts) gets replaced."""
    text = "**James Blue** (10:37 PM):\nsome body"
    out = strip_sender_timestamp_patterns(text)
    assert "**James Blue**" not in out
    assert "[paraphrased: James Blue @ 10:37 PM]" in out


def test_strip_plain_sender_pattern():
    """Plain `Sender (HH:MM): body` line gets replaced."""
    text = "James Blue (10:37 PM): Spec content here"
    out = strip_sender_timestamp_patterns(text)
    assert "[paraphrased: James Blue @ 10:37 PM]" in out
    assert "James Blue (10:37 PM):" not in out


def test_strip_blockquote_bold_sender():
    """`> **Sender (HH:MM):**` (markdown blockquote prefix) also caught."""
    text = "> **James Blue (10:37 PM):**\nThe spec list..."
    out = strip_sender_timestamp_patterns(text)
    assert "**James Blue (10:37 PM):**" not in out
    assert "[paraphrased: James Blue @ 10:37 PM]" in out


def test_strip_24_hour_timestamps():
    """24h format `(14:37)` is also a timestamp shape; should match."""
    text = "**James Blue (14:37):** content"
    out = strip_sender_timestamp_patterns(text)
    assert "[paraphrased: James Blue @ 14:37]" in out


def test_strip_seconds_in_timestamp():
    """`(10:37:42 PM)` with seconds also matches."""
    text = "Alice (10:37:42 PM): body"
    out = strip_sender_timestamp_patterns(text)
    assert "[paraphrased: Alice @ 10:37:42 PM]" in out


def test_strip_with_timezone_suffix():
    """`(10:37 PM PDT)` with timezone marker matches."""
    text = "**Alice (10:37 PM PDT):** body"
    out = strip_sender_timestamp_patterns(text)
    assert "[paraphrased: Alice @ 10:37 PM PDT]" in out


def test_strip_preserves_prose_without_timestamp_match():
    """Plain prose mentioning a person/time without the strict pattern
    is unchanged. E.g. `met John (yesterday)` doesn't match HH:MM."""
    text = "Met John (yesterday) at the cafe."
    out = strip_sender_timestamp_patterns(text)
    assert out == text


def test_strip_preserves_inline_bold_text():
    """`**bold**` mid-sentence (no parenthesized ts) is NOT mangled."""
    text = "The **most important** thing is X."
    out = strip_sender_timestamp_patterns(text)
    assert out == text


def test_strip_empty_input_returns_unchanged():
    assert strip_sender_timestamp_patterns("") == ""
    assert strip_sender_timestamp_patterns(None) is None


def test_strip_multiple_occurrences_all_replaced():
    text = (
        "**Alice (9:00 AM):** first message\n"
        "**Bob (9:01 AM):** reply\n"
        "**Alice (9:02 AM):** thanks"
    )
    out = strip_sender_timestamp_patterns(text)
    assert out.count("[paraphrased:") == 3
    assert "Alice (9:00 AM)" not in out
    assert "Bob (9:01 AM)" not in out


# ─── wrap_paraphrase ─────────────────────────────────────────────────────


def test_wrap_adds_header_and_footer():
    out = wrap_paraphrase("some content", memory_type="session-log")
    assert out.startswith("[CACHED PARAPHRASE")
    assert out.endswith("[END CACHED PARAPHRASE]")
    assert "some content" in out


def test_wrap_includes_type_and_title_in_header():
    out = wrap_paraphrase(
        "x", memory_type="session-log", title="Daily summary — 2026-05-23",
    )
    assert "type=session-log" in out
    assert "Daily summary — 2026-05-23" in out


def test_wrap_idempotent_when_already_wrapped():
    """Double-wrap returns the input unchanged."""
    first = wrap_paraphrase("body", memory_type="session-log")
    second = wrap_paraphrase(first, memory_type="session-log")
    assert second == first


def test_wrap_empty_input_unchanged():
    assert wrap_paraphrase("") == ""
    assert wrap_paraphrase(None) is None


# ─── sanitize_recall_content (full pipeline) ─────────────────────────────


def test_sanitize_session_log_strips_and_wraps():
    content = (
        "James provided a detailed spec:\n"
        "**James Blue (10:37 PM):**\n"
        "the 6-city list"
    )
    out = sanitize_recall_content(content, "session-log",
                                   title="Daily summary — 2026-05-23")
    # Both transforms applied:
    assert out.startswith("[CACHED PARAPHRASE")
    assert "[paraphrased: James Blue @ 10:37 PM]" in out
    assert "**James Blue (10:37 PM):**" not in out
    # Body still readable:
    assert "the 6-city list" in out
    assert "James provided a detailed spec" in out


def test_sanitize_fact_type_passes_through_unchanged():
    """'fact' is now authoritative — content passes through without paraphrase wrapping."""
    content = "Vato (3:23 PM): clarification message"
    out = sanitize_recall_content(content, "fact")
    assert out == content
    assert "[CACHED PARAPHRASE" not in out


def test_sanitize_project_type_passes_through_unchanged():
    """`project` is authoritative — content is the user's own decisions,
    not paraphrased from a channel — pass through clean."""
    content = "**Decision (10:37 PM):** ship the bot"
    out = sanitize_recall_content(content, "project")
    assert out == content
    assert "CACHED PARAPHRASE" not in out


def test_sanitize_user_type_passes_through_unchanged():
    out = sanitize_recall_content("the user is X", "user")
    assert out == "the user is X"


def test_sanitize_feedback_type_passes_through_unchanged():
    out = sanitize_recall_content("the user said never do Y", "feedback")
    assert out == "the user said never do Y"


def test_sanitize_reference_type_passes_through_unchanged():
    out = sanitize_recall_content(
        "the dashboard is at https://example.com", "reference",
    )
    assert "CACHED PARAPHRASE" not in out
    assert out == "the dashboard is at https://example.com"


def test_sanitize_null_type_fail_closed():
    """NULL `memory_type` = paraphrase-class (fail closed) — sanitize."""
    out = sanitize_recall_content("Alice (10:00 AM): hi", None)
    assert out.startswith("[CACHED PARAPHRASE")
    assert "[paraphrased: Alice @ 10:00 AM]" in out


def test_sanitize_none_content_returns_empty_string():
    assert sanitize_recall_content(None, "session-log") == ""


def test_sanitize_empty_content_returns_empty():
    assert sanitize_recall_content("", "session-log") == ""


def test_sanitize_today_real_leak_pattern_blocked():
    """End-to-end: the exact 2026-05-23 daily-log shape that
    contaminated the brain in the user's 1:00 PM turn must come
    out fully neutralized."""
    content = (
        "## Last Upwork Conversation — James Blue\n\n"
        "**James Blue (10:37 PM):**\n"
        "> spec spec spec — 6 cities, 5-day turn, auto-accept\n\n"
        "**James Blue (10:37 PM):**\n"
        "> Upon successful completion, I will award you another project\n"
    )
    out = sanitize_recall_content(
        content, "session-log",
        title="Daily summary — 2026-05-23",
    )
    # Wrap framing present:
    assert out.startswith("[CACHED PARAPHRASE")
    assert "NOT a live channel quote" in out
    # Both sender-timestamp blocks mangled:
    assert out.count("[paraphrased: James Blue @ 10:37 PM]") == 2
    # The original bold-block patterns are gone — brain can't lift them:
    assert "**James Blue (10:37 PM):**" not in out
    # Bodies preserved so the brain still has the substance:
    assert "spec spec spec" in out
    assert "another project" in out
