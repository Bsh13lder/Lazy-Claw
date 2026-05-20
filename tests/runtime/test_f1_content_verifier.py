"""Unit + integration tests for F1 phase-2 content verifier.

Phase-2 closes the gap structural-only F1 leaves open: drafts that DO
contain `> sender (ts): content` quote blocks but quote text that never
appeared in any tool result this turn (e.g. today's 13:34 incident where
a bad room_id made upwork_get_conversation scrape unrelated DOM).

The verifier is observation-only at module-load time (``F1_PHASE2_ENFORCE
= False``). These tests pin both the pure parsing/matching logic AND the
wiring contract — the observer logs WARN on unverified quotes but never
blocks the reply while the flag is False.
"""

from __future__ import annotations

import logging

import pytest

from lazyclaw.runtime import f1_content_verifier as f1v
from lazyclaw.runtime.f1_content_verifier import (
    F1_PHASE2_ENFORCE,
    QuoteTuple,
    VerificationReport,
    observe_or_enforce,
    parse_quote_lines,
    verify_quotes_against_tool_results,
)


# ─── module-level invariants ──────────────────────────────────────────────


def test_phase2_enforce_is_off_by_default() -> None:
    """Observation-only mode is the contract — must stay False until
    telemetry calibration is done."""
    assert F1_PHASE2_ENFORCE is False


# ─── parse_quote_lines — 5 cases ─────────────────────────────────────────


def test_parse_clean_shape() -> None:
    reply = (
        "> James Blue (10:37 PM): Narrowed the city list to 6.\n"
        "> James Blue (10:30 PM): Original 20-city scope was too broad.\n"
        "Summary follows below.\n"
    )
    quotes = parse_quote_lines(reply)
    assert len(quotes) == 2
    assert quotes[0].sender == "James Blue"
    assert quotes[0].timestamp == "10:37 PM"
    assert quotes[0].content == "Narrowed the city list to 6."
    assert quotes[0].line_no == 1
    assert quotes[1].line_no == 2


def test_parse_em_dash_separator() -> None:
    # Some brains output `> sender (ts) — content` (em-dash) instead of `:`
    reply = "> Vato (today 14:02) — Send the proposal at 5pm.\n"
    quotes = parse_quote_lines(reply)
    assert len(quotes) == 1
    assert quotes[0].sender == "Vato"
    assert quotes[0].timestamp == "today 14:02"
    assert quotes[0].content == "Send the proposal at 5pm."


def test_parse_skips_lines_without_timestamp() -> None:
    # Lines without a `(...)` group are not well-formed quotes — skipped.
    reply = (
        "> James said hello\n"
        "> James Blue (10:37 PM): Real quote line.\n"
        "> No timestamp here either\n"
    )
    quotes = parse_quote_lines(reply)
    assert len(quotes) == 1
    assert quotes[0].sender == "James Blue"


def test_parse_multi_line_reply_with_interleaved_prose() -> None:
    reply = (
        "Here's what I see in the thread:\n"
        "\n"
        "> Alice (2026-05-17 14:01): The deadline shifted to Friday.\n"
        "\n"
        "Some commentary in the middle.\n"
        "\n"
        "> Bob (2026-05-17 14:05): Confirmed Friday EOD.\n"
        "\n"
        "End of summary.\n"
    )
    quotes = parse_quote_lines(reply)
    assert len(quotes) == 2
    assert quotes[0].sender == "Alice"
    assert quotes[1].sender == "Bob"
    # Line numbers should be 1-based against the original reply.
    assert quotes[0].line_no == 3
    assert quotes[1].line_no == 7


def test_parse_handles_escaped_chars_in_content() -> None:
    # Apostrophes, quotes, and unicode in content should pass through.
    reply = (
        "> James (10:37 PM): Don't worry — \"6 cities\" is fine. 👍\n"
    )
    quotes = parse_quote_lines(reply)
    assert len(quotes) == 1
    assert quotes[0].content == "Don't worry — \"6 cities\" is fine. 👍"


def test_parse_empty_input_returns_empty_list() -> None:
    assert parse_quote_lines("") == []
    assert parse_quote_lines(None) == []  # type: ignore[arg-type]


# ─── verify_quotes_against_tool_results — 5 cases ────────────────────────


def test_verify_all_quotes_match() -> None:
    quotes = [
        QuoteTuple("James", "10:37 PM", "Narrowed the city list to 6.", 1),
        QuoteTuple("James", "10:30 PM", "Original 20-city scope.", 2),
    ]
    tool_results = [
        "[LIVE READ] James Blue: Narrowed the city list to 6.\n"
        "James Blue: Original 20-city scope.\n[END LIVE READ]",
    ]
    report = verify_quotes_against_tool_results(quotes, tool_results)
    assert report.total_quotes == 2
    assert report.verified_count == 2
    assert report.unverified_count == 0
    assert report.unverified_quotes == ()


def test_verify_no_quotes_match() -> None:
    quotes = [
        QuoteTuple("Ghost", "00:00", "This was never said anywhere.", 1),
        QuoteTuple("Ghost", "00:01", "Neither was this fabrication.", 2),
    ]
    tool_results = ["unrelated content about a different conversation"]
    report = verify_quotes_against_tool_results(quotes, tool_results)
    assert report.total_quotes == 2
    assert report.verified_count == 0
    assert report.unverified_count == 2
    assert len(report.unverified_quotes) == 2


def test_verify_partial_match() -> None:
    quotes = [
        QuoteTuple("James", "10:37 PM", "Narrowed the city list to 6.", 1),
        QuoteTuple("James", "10:40 PM", "Hallucinated dollar amount $150.", 2),
    ]
    tool_results = ["James Blue: Narrowed the city list to 6 cities."]
    report = verify_quotes_against_tool_results(quotes, tool_results)
    assert report.total_quotes == 2
    assert report.verified_count == 1
    assert report.unverified_count == 1
    assert report.unverified_quotes[0][0].sender == "James"
    assert "Hallucinated" in report.unverified_quotes[0][0].content


def test_verify_empty_inputs() -> None:
    # No quotes — vacuous pass.
    report = verify_quotes_against_tool_results([], ["any tool result"])
    assert report.total_quotes == 0
    assert report.verified_count == 0
    assert report.unverified_count == 0
    # Quotes present but no tool results — all unverified.
    quotes = [QuoteTuple("J", "10:00", "Some substantive claim here.", 1)]
    report2 = verify_quotes_against_tool_results(quotes, [])
    assert report2.total_quotes == 1
    assert report2.unverified_count == 1
    assert "no tool results" in report2.unverified_quotes[0][1]


def test_verify_case_insensitive_and_whitespace_collapse() -> None:
    # Quote uses different casing + double spaces vs tool output.
    quotes = [
        QuoteTuple(
            "James", "10:37 PM",
            "NARROWED   THE  CITY LIST to 6.", 1,
        ),
    ]
    tool_results = [
        "james blue\n said: narrowed the city list to 6 cities yesterday.",
    ]
    report = verify_quotes_against_tool_results(quotes, tool_results)
    assert report.verified_count == 1
    assert report.unverified_count == 0


def test_verify_short_quote_skipped_as_verified() -> None:
    # Quotes under _MIN_PROBE_CHARS are counted as verified — too short to
    # be a meaningful factual claim.
    quotes = [QuoteTuple("James", "10:37 PM", "OK", 1)]
    report = verify_quotes_against_tool_results(quotes, ["unrelated data"])
    assert report.verified_count == 1
    assert report.unverified_count == 0


def test_verification_report_summary_format() -> None:
    quotes = [
        QuoteTuple("J", "10:00", "Real quote that matches the tool.", 1),
        QuoteTuple("J", "10:01", "Fabricated quote that doesn't match.", 2),
    ]
    tool_results = ["Real quote that matches the tool"]
    report = verify_quotes_against_tool_results(quotes, tool_results)
    summary = report.summary()
    assert "1/2" in summary
    assert "1 unverified" in summary


# ─── integration: observe_or_enforce wiring ──────────────────────────────


def test_observe_logs_warning_on_unverified_quotes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: parse + verify + log WARN on unverified, never block
    while ``F1_PHASE2_ENFORCE`` is False (which it must be by default)."""
    assert F1_PHASE2_ENFORCE is False, (
        "Phase-2 must ship in observation mode; flip the flag in a "
        "follow-up commit after telemetry calibration."
    )

    reply = (
        "Here's the thread:\n"
        "\n"
        "> James (10:37 PM): Narrowed the city list to 6 cities.\n"
        "> James (10:40 PM): Confirmed Friday EOD deadline.\n"
        "> James (10:45 PM): Agreed $150 budget for a Windows laptop.\n"
        "\n"
        "Summary follows.\n"
    )
    # Two quotes appear in tool results; the third is hallucinated.
    tool_results = [
        "[LIVE READ @ upwork] James Blue: Narrowed the city list to "
        "6 cities. Confirmed Friday EOD deadline. [END LIVE READ]"
    ]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=f1v.__name__):
        report, should_block = observe_or_enforce(reply, tool_results)

    assert report.total_quotes == 3
    assert report.verified_count == 2
    assert report.unverified_count == 1
    # Observation-only — must NEVER block while the flag is False.
    assert should_block is False
    # WARN log must mention the unverified count and include a sample of
    # the fabricated quote.
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1, f"expected 1 WARN, got {len(warns)}"
    msg = warns[0].getMessage()
    assert "[F1-phase2-obs]" in msg
    assert "1 unverified" in msg
    assert "Agreed $150" in msg


def test_observe_silent_when_all_verified(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = "> J (10:00): The quoted text appears in the tool result.\n"
    tool_results = ["the quoted text appears in the tool result"]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=f1v.__name__):
        report, should_block = observe_or_enforce(reply, tool_results)
    assert report.unverified_count == 0
    assert should_block is False
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warns == []


def test_observe_never_blocks_when_flag_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Direct contract test for the observation-only guarantee."""
    reply = "> Ghost (00:00): Completely fabricated content.\n"
    tool_results = ["totally unrelated tool output"]
    with caplog.at_level(logging.WARNING, logger=f1v.__name__):
        _, should_block = observe_or_enforce(reply, tool_results)
    assert should_block is False
