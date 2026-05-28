"""Tests for the typed-memory taxonomy in lazyclaw.lazybrain.memory_types.

The classifier is the foundation for the "stop pre-injecting paraphrases
into Claude's cached layer" rewrite. Every contamination class downstream
relies on ``infer_memory_type`` returning the right scope label so the
context builder can filter cleanly via ``is_auto_inject_type``.

These tests pin the deterministic classifier shape, the closed taxonomy,
and the auto-inject filter logic. The DB layer (notes.memory_type column
+ migration + save_note plumbing) is exercised by the lazybrain store
integration tests; this file is the pure-function suite.
"""

from __future__ import annotations

import pytest

from lazyclaw.lazybrain import memory_types as mt


# ─── Taxonomy invariants ──────────────────────────────────────────────────


def test_memory_types_is_closed_set_of_seven() -> None:
    assert mt.MEMORY_TYPES == {
        "user", "feedback", "project", "reference",
        "fact", "session-log", "other",
    }


def test_auto_inject_types_excludes_session_log_and_other_but_keeps_fact() -> None:
    """Load-bearing assertion: session-log paraphrases must NEVER be in
    the auto-inject set. ``other`` is also out (opaque catch-all). ``fact``
    is IN — it's the default for genuine knowledge saved via save_memory /
    lazybrain_save_note, and excluding it silently dropped every new
    agent memory from the cached system-prompt layer (fixed Group A 2026-05)."""
    assert mt.AUTO_INJECT_TYPES == {
        "user", "feedback", "project", "reference", "fact",
    }
    assert "session-log" not in mt.AUTO_INJECT_TYPES
    assert "other" not in mt.AUTO_INJECT_TYPES
    assert "fact" in mt.AUTO_INJECT_TYPES
    assert "other" not in mt.AUTO_INJECT_TYPES
    assert mt.AUTO_INJECT_TYPES.issubset(mt.MEMORY_TYPES)


def test_default_memory_type_is_fact() -> None:
    assert mt.DEFAULT_MEMORY_TYPE == "fact"
    assert mt.DEFAULT_MEMORY_TYPE in mt.MEMORY_TYPES


# ─── infer_memory_type — tag-based dispatch ───────────────────────────────


@pytest.mark.parametrize(
    "tags, expected",
    [
        (["feedback"], "feedback"),
        (["correction"], "feedback"),
        (["rule"], "feedback"),
        (["user-correction"], "feedback"),
        (["preference"], "feedback"),
        (["user"], "user"),
        (["profile"], "user"),
        (["about-me"], "user"),
        (["project"], "project"),
        (["goal"], "project"),
        (["plan"], "project"),
        (["decision"], "project"),
        (["architecture"], "project"),
        (["reference"], "reference"),
        (["url"], "reference"),
        (["dashboard"], "reference"),
        (["contact"], "reference"),
        (["session-end"], "session-log"),
        (["daily-log"], "session-log"),
        (["channel-thread"], "session-log"),
        (["background_done"], "session-log"),
        # Case-insensitive tag matching.
        (["FEEDBACK"], "feedback"),
        (["Decision"], "project"),
    ],
)
def test_classifier_dispatches_on_tags(
    tags: list[str], expected: str,
) -> None:
    assert mt.infer_memory_type("some content", tags=tags) == expected


def test_classifier_returns_default_when_no_signal() -> None:
    assert mt.infer_memory_type("just some random fact about chemistry") == "fact"
    assert mt.infer_memory_type("", tags=[]) == "fact"
    assert mt.infer_memory_type("hello world", tags=None, title=None) == "fact"


# ─── infer_memory_type — phrase-based fallback ────────────────────────────


@pytest.mark.parametrize(
    "head_text, expected",
    [
        ("Rule: never paraphrase channel data.", "feedback"),
        ("Stop summarizing at the end of every reply.", "feedback"),
        ("Never use run_background for code work.", "feedback"),
        ("Always quote the tool result verbatim.", "feedback"),
        ("Don't add Co-Authored-By to commits.", "feedback"),
        ("From now on, route code via claude-code MCP.", "feedback"),
        ("User prefers Sonnet over Opus.", "feedback"),
        ("Goal: ship F1 grounding by Friday.", "project"),
        ("Project: lazyclaw — Phase 18.", "project"),
        ("Decision: drop daily_log injection on Claude routes.", "project"),
        ("Phase 1 — typed memory taxonomy.", "project"),
        ("URL: https://docs.claude.com/citations", "reference"),
        ("Link: https://example.com/dashboard", "reference"),
        ("Repo: github.com/anthropics/claude-code", "reference"),
        ("## Last Upwork Conversation\nContact: James", "session-log"),
        ("## Session summary — 2026-05-19", "session-log"),
        ("Background task done — scaffold complete.", "session-log"),
        ("Weekly summary of activity.", "session-log"),
    ],
)
def test_classifier_dispatches_on_title_phrases(
    head_text: str, expected: str,
) -> None:
    assert mt.infer_memory_type("body", title=head_text) == expected


def test_classifier_uses_content_first_line_when_no_title() -> None:
    body = "Stop using emojis in commits.\n\nMore context follows here."
    assert mt.infer_memory_type(body) == "feedback"


def test_classifier_session_log_phrase_beats_other_phrases() -> None:
    """A daily_log header that incidentally contains a feedback-shape
    phrase later in the title must still classify as session-log so the
    auto-inject filter excludes it."""
    head = "## Last Upwork Conversation — User said never quote me"
    assert mt.infer_memory_type("body", title=head) == "session-log"


def test_classifier_tag_signal_beats_phrase_signal() -> None:
    """Tags are the explicit author signal — they must dominate phrase
    inference. A user tagging a note ``reference`` overrides a feedback-
    shape opening line."""
    assert mt.infer_memory_type(
        "Stop being verbose.", tags=["reference"], title="Stop being verbose.",
    ) == "reference"


def test_classifier_never_raises_on_none_or_empty() -> None:
    assert mt.infer_memory_type("") == "fact"
    assert mt.infer_memory_type("", tags=None, title=None) == "fact"
    assert mt.infer_memory_type("body", tags=[]) == "fact"
    # Whitespace-only content fell into a splitlines() edge case in an
    # earlier draft — pin the safe behaviour here.
    assert mt.infer_memory_type("   ") == "fact"


# ─── Normalization + auto-inject gate ─────────────────────────────────────


def test_normalize_passes_known_values_through() -> None:
    for t in mt.MEMORY_TYPES:
        assert mt.normalize_memory_type(t) == t


def test_normalize_lowercases_and_dashifies() -> None:
    assert mt.normalize_memory_type("FEEDBACK") == "feedback"
    assert mt.normalize_memory_type("Session_Log") == "session-log"
    assert mt.normalize_memory_type("  Project  ") == "project"


def test_normalize_unknown_collapses_to_default() -> None:
    assert mt.normalize_memory_type("garbage") == "fact"
    assert mt.normalize_memory_type("") == "fact"
    assert mt.normalize_memory_type(None) == "fact"
    assert mt.normalize_memory_type("   ") == "fact"


def test_is_auto_inject_type_fails_closed_on_none_and_unknown() -> None:
    """The cached system prompt must be safe by default — a row whose
    memory_type is NULL (pre-migration, or a write path that didn't pass
    the new arg) must NOT be auto-injected. Same for typos / drift.
    ``fact`` is the explicit exception: it IS auto-inject (fixed Group A
    2026-05); see test_auto_inject_types_excludes_session_log_and_other_but_keeps_fact."""
    assert mt.is_auto_inject_type(None) is False
    assert mt.is_auto_inject_type("session-log") is False
    assert mt.is_auto_inject_type("other") is False
    assert mt.is_auto_inject_type("typo") is False
    assert mt.is_auto_inject_type("") is False
    # ``fact`` is intentionally INCLUDED — verified positively here so a
    # regression that removes it from AUTO_INJECT_TYPES trips this test.
    assert mt.is_auto_inject_type("fact") is True


@pytest.mark.parametrize(
    "memory_type", ["user", "feedback", "project", "reference"],
)
def test_is_auto_inject_type_passes_canonical_set(memory_type: str) -> None:
    assert mt.is_auto_inject_type(memory_type) is True


# ─── End-to-end sanity: the 2026-05-19 16:19 contamination header ─────────


def test_real_incident_daily_log_header_classifies_as_session_log() -> None:
    """The exact 2026-05-16 daily_log header that contaminated the brain
    on 2026-05-19 16:19 must now classify as ``session-log`` so the
    context builder filter excludes it from the cached system prompt.
    """
    head = (
        "## Last Upwork Conversation\n"
        "**Contact:** James Blue\n"
        "**Thread Time:** 10:34 PM → 12:21 AM\n"
        "### Messages:\n"
        "1. **Vato (you):** Pitched undetectable browser via CDP."
    )
    assert mt.infer_memory_type(head) == "session-log"
    assert mt.is_auto_inject_type(mt.infer_memory_type(head)) is False
