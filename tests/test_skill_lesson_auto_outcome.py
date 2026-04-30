"""Tests for runtime.skill_lesson_auto.outcome_from_result.

Covers two paired bug fixes:
1. Broader failure detection — `[MCP ERROR]`, `Failed to`, `Timeout`,
   `[mcp-...]` channel headers must be flagged as `failed`. Previously
   only literal `Error:` / `ERROR:` prefixes did, so most real failures
   silently became `pending` cards that the verification pump promoted
   to `verified` — broken shapes the brain happily replayed.
2. Result snippet returned — every outcome (failed, pending) now
   carries the first ~600 chars of the actual tool output so the
   lesson card has a real "what came back" record.
"""

from __future__ import annotations

from lazyclaw.runtime.skill_lesson_auto import outcome_from_result


class TestStrictPrefixDetection:
    """Original failure paths (regression guard)."""

    def test_error_colon_prefix_marks_failed(self):
        outcome, err, snip = outcome_from_result("Error: something broke", None)
        assert outcome == "failed"
        assert err == "Error: something broke"
        assert snip == "Error: something broke"

    def test_error_caps_prefix_marks_failed(self):
        outcome, err, _ = outcome_from_result("ERROR: boom", None)
        assert outcome == "failed"
        assert "ERROR: boom" in err

    def test_exception_marks_failed(self):
        outcome, err, snip = outcome_from_result(
            None, RuntimeError("connection lost"),
        )
        assert outcome == "failed"
        assert "RuntimeError" in err
        assert "connection lost" in err
        # snippet equals the formatted error msg on the exception path
        assert snip == err


class TestBroadenedSubstringDetection:
    """The actual bug fix — these used to fall through to ``pending``."""

    def test_mcp_error_header_marks_failed(self):
        text = "[MCP ERROR] scraper pool exhausted on search_google"
        outcome, err, snip = outcome_from_result(text, None)
        assert outcome == "failed"
        assert err.startswith("[MCP ERROR]")
        assert "scraper pool exhausted" in err
        assert snip == text

    def test_mcp_channel_header_marks_failed(self):
        text = "[mcp-scraper | search]  [MCP ERROR] scraper pool exhausted"
        outcome, err, _ = outcome_from_result(text, None)
        assert outcome == "failed"
        # err is anchored at the first match — channel header
        assert err.startswith("[mcp-")

    def test_failed_to_pattern_marks_failed(self):
        outcome, err, _ = outcome_from_result(
            "Failed to load page after 30s", None,
        )
        assert outcome == "failed"
        assert "Failed to" in err

    def test_timeout_pattern_marks_failed(self):
        outcome, err, _ = outcome_from_result("Timeout: request took >30s", None)
        assert outcome == "failed"
        assert "Timeout" in err

    def test_traceback_pattern_marks_failed(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1\n'
            "ValueError: bad input"
        )
        outcome, err, _ = outcome_from_result(text, None)
        assert outcome == "failed"
        assert "Traceback" in err

    def test_unable_to_pattern_marks_failed(self):
        outcome, _, _ = outcome_from_result(
            "Unable to locate selector .submit", None,
        )
        assert outcome == "failed"

    def test_pool_exhausted_marks_failed(self):
        outcome, _, _ = outcome_from_result(
            "Operation aborted: pool exhausted, try again later", None,
        )
        assert outcome == "failed"


class TestPendingPath:
    """Successful results still get the new snippet — fail-open."""

    def test_plain_success_pending_with_snippet(self):
        outcome, err, snip = outcome_from_result(
            "Saved 3 records to LazyBrain.", None,
        )
        assert outcome == "pending"
        assert err is None
        assert snip == "Saved 3 records to LazyBrain."

    def test_long_success_truncates_snippet_to_600(self):
        text = "ok " * 500
        outcome, _, snip = outcome_from_result(text, None)
        assert outcome == "pending"
        assert len(snip) == 600

    def test_empty_result_no_snippet(self):
        outcome, err, snip = outcome_from_result(None, None)
        assert outcome == "pending"
        assert err is None
        assert snip is None


class TestNoFalsePositive:
    """Make sure benign success messages don't trip the substring scan."""

    def test_word_failed_inside_long_success_after_window(self):
        # Substring scan only checks first 400 chars. A "failed" appearing
        # at char 500+ in a giant success body should NOT trigger.
        head = "Successfully completed task. " * 15  # ~450 chars of "Successfully..."
        text = head + "earlier the validation check failed but recovered"
        outcome, _, _ = outcome_from_result(text, None)
        assert outcome == "pending"

    def test_approval_required_short_circuits_to_skip(self):
        outcome, err, snip = outcome_from_result(
            "APPROVAL_REQUIRED: confirm send", None,
        )
        assert outcome == "skip"
        assert err is None
        assert snip is None
