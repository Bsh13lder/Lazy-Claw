"""Structured specialist failure reports (Claude Code orchestrator pattern).

2026-06-10 00:33 incident: ``freelance_specialist`` was killed by the
stuck-detector (``Called 'search_tools' 4 times in a row without
progress.``) and the brain's consolidation turn received an opaque
``[Stuck: loop] ...`` blob — it shipped a false "✅ done" / vague apology
instead of deciding the next move.

The fix: on any failure-shaped termination (stuck loop, exception,
cancellation, every-tool-errored), ``run_specialist`` records a
clearly-delimited ``[SPECIALIST FAILURE REPORT]`` block built from the
tool history the runner already tracks. The consolidating brain can then
re-delegate, answer from partials, or report the blocker precisely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.llm.providers.base import LLMResponse, ToolCall
from lazyclaw.teams.failure_report import (
    FAILURE_REPORT_MARKER,
    OUTCOME_ALL_TOOLS_FAILED,
    OUTCOME_CANCELLED,
    OUTCOME_EXCEPTION,
    OUTCOME_STUCK_LOOP,
    ToolAttempt,
    all_tools_failed,
    build_tool_attempts,
    extract_failure_report,
    last_progress,
    render_failure_report,
)
from lazyclaw.teams.runner import run_specialist
from lazyclaw.teams.specialist import SpecialistConfig


# ── Pure helpers ──────────────────────────────────────────────────────


class TestBuildToolAttempts:
    def test_empty_history(self):
        assert build_tool_attempts([], []) == ()

    def test_aggregates_by_name_preserving_first_seen_order(self):
        attempts = build_tool_attempts(
            ["search_tools", "browser", "search_tools"],
            ["ok1", "ok2", "ok3"],
        )
        assert [a.name for a in attempts] == ["search_tools", "browser"]
        assert attempts[0].count == 2
        assert attempts[1].count == 1

    def test_last_error_captured_from_most_recent_call(self):
        attempts = build_tool_attempts(
            ["browser", "browser"],
            ["Error: timeout", "Error: connection refused"],
        )
        assert attempts[0].last_error.endswith("Error: connection refused")

    def test_last_error_cleared_when_latest_call_succeeds(self):
        attempts = build_tool_attempts(
            ["browser", "browser"],
            ["Error: timeout", "page loaded fine"],
        )
        assert attempts[0].last_error == ""

    def test_last_error_tail_capped(self):
        long_err = "Error: " + "x" * 1000
        attempts = build_tool_attempts(["browser"], [long_err])
        assert len(attempts[0].last_error) <= 200

    def test_mismatched_lengths_do_not_raise(self):
        # More history than results (a crash mid-iteration) must degrade,
        # never raise.
        attempts = build_tool_attempts(["a", "b"], ["only one"])
        assert [a.name for a in attempts] == ["a", "b"]

    def test_non_string_results_coerced(self):
        attempts = build_tool_attempts(["a"], [{"weird": True}])
        assert attempts[0].count == 1

    def test_immutable_attempt(self):
        attempt = ToolAttempt(name="x", count=1)
        with pytest.raises(Exception):
            attempt.count = 2  # type: ignore[misc]


class TestAllToolsFailed:
    def test_no_tools_is_not_all_failed(self):
        assert all_tools_failed([], []) is False

    def test_all_errors(self):
        assert all_tools_failed(
            ["a", "b"], ["Error: x", "Error: y"],
        ) is True

    def test_one_success_breaks_it(self):
        assert all_tools_failed(
            ["a", "b"], ["Error: x", "fine"],
        ) is False


class TestLastProgress:
    def test_empty(self):
        assert last_progress([], []) == ""

    def test_picks_most_recent_non_error(self):
        out = last_progress(
            ["a", "b", "c"],
            ["first ok", "second ok", "Error: boom"],
        )
        assert out.startswith("b:")
        assert "second ok" in out

    def test_all_errors_returns_empty(self):
        assert last_progress(["a"], ["Error: nope"]) == ""

    def test_capped_at_200_chars_of_result(self):
        out = last_progress(["a"], ["y" * 1000])
        assert len(out) <= 220  # "a: " prefix + 200-char summary


class TestRenderFailureReport:
    def test_contains_marker_and_fields(self):
        report = render_failure_report(
            specialist="freelance_specialist",
            task="Find new Upwork jobs and apply",
            outcome=OUTCOME_STUCK_LOOP,
            tool_history=["search_tools"] * 4,
            tool_results=["nothing useful 1", "meh 2", "zilch 3", "nada 4"],
            detail="Called 'search_tools' 4 times in a row without progress.",
        )
        assert report.startswith(FAILURE_REPORT_MARKER)
        assert "specialist: freelance_specialist" in report
        assert "task: Find new Upwork jobs and apply" in report
        assert f"outcome: {OUTCOME_STUCK_LOOP}" in report
        assert "search_tools (x4" in report
        assert "last_progress:" in report

    def test_task_preview_capped(self):
        report = render_failure_report(
            specialist="s", task="t" * 1000, outcome=OUTCOME_EXCEPTION,
        )
        task_line = next(
            ln for ln in report.splitlines() if ln.startswith("task:")
        )
        assert len(task_line) <= 220

    def test_detail_rendered_when_given(self):
        report = render_failure_report(
            specialist="s", task="t", outcome=OUTCOME_EXCEPTION,
            detail="RuntimeError: LLM exploded",
        )
        assert "detail: RuntimeError: LLM exploded" in report

    def test_no_tools_renders_none_placeholder(self):
        report = render_failure_report(
            specialist="s", task="t", outcome=OUTCOME_CANCELLED,
        )
        assert "tools_attempted: (none)" in report
        assert "last_progress: (none)" in report

    def test_render_never_raises_on_garbage(self):
        report = render_failure_report(
            specialist="s", task=None,  # type: ignore[arg-type]
            outcome=OUTCOME_EXCEPTION,
            tool_history=[None, 5],  # type: ignore[list-item]
            tool_results=[object()],
        )
        assert FAILURE_REPORT_MARKER in report


class TestExtractFailureReport:
    def test_roundtrip(self):
        report = render_failure_report(
            specialist="s", task="t", outcome=OUTCOME_ALL_TOOLS_FAILED,
            tool_history=["a"], tool_results=["Error: x"],
        )
        text = "I tried my best.\n\n" + report
        assert extract_failure_report(text) == report

    def test_missing_marker_returns_empty(self):
        assert extract_failure_report("nothing here") == ""

    def test_empty_input(self):
        assert extract_failure_report("") == ""
        assert extract_failure_report(None) == ""  # type: ignore[arg-type]


# ── run_specialist integration ────────────────────────────────────────


_SPEC = SpecialistConfig(
    name="freelance_specialist",
    display_name="Freelance Specialist",
    system_prompt="You hunt gigs.",
    allowed_skills=("search_tools", "browser"),
)


@pytest.fixture
def fake_registry():
    r = MagicMock()
    r.list_tools.return_value = [
        {"function": {"name": "search_tools", "description": "find tools"}},
        {"function": {"name": "browser", "description": "drive browser"}},
    ]
    r.list_mcp_tools.return_value = []
    return r


def _tool_call_response(name: str, args: dict, call_id: str) -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        model="test-model",
    )


@pytest.mark.asyncio
async def test_stuck_loop_produces_structured_report(fake_registry):
    """search_tools called 4× in a row (the 2026-06-10 incident shape)
    must terminate with outcome=stuck_loop and a per-tool attempts list."""
    router = MagicMock()
    router.chat = AsyncMock(side_effect=[
        _tool_call_response("search_tools", {"query": f"q{i}"}, f"tc_{i}")
        for i in range(4)
    ])
    with patch("lazyclaw.teams.runner.ToolExecutor") as MockExec:
        # Varying non-error results so ONLY the loop detector fires
        # (identical results would trip same_result; errors would trip
        # repeated_error).
        MockExec.return_value.execute = AsyncMock(side_effect=[
            "a" * 10, "b" * 60, "c" * 250, "d" * 900,
        ])
        result = await run_specialist(
            user_id="u1",
            specialist=_SPEC,
            task="Find new Upwork jobs and apply to the best matches",
            registry=fake_registry,
            eco_router=router,
            permission_checker=None,
        )

    assert result.success is False
    assert FAILURE_REPORT_MARKER in result.result
    assert f"outcome: {OUTCOME_STUCK_LOOP}" in result.result
    assert "search_tools (x4" in result.result
    assert "specialist: freelance_specialist" in result.result
    assert "Find new Upwork jobs" in result.result
    # last_progress carries the most recent successful tool output
    assert "last_progress: search_tools" in result.result
    # error stays the short human-readable context for legacy consumers
    assert "4 times in a row" in (result.error or "")


@pytest.mark.asyncio
async def test_exception_produces_structured_report(fake_registry):
    router = MagicMock()
    router.chat = AsyncMock(side_effect=RuntimeError("LLM exploded"))
    result = await run_specialist(
        user_id="u1",
        specialist=_SPEC,
        task="do a thing",
        registry=fake_registry,
        eco_router=router,
        permission_checker=None,
    )
    assert result.success is False
    assert result.error == "LLM exploded"
    assert FAILURE_REPORT_MARKER in result.result
    assert f"outcome: {OUTCOME_EXCEPTION}" in result.result
    assert "detail: RuntimeError: LLM exploded" in result.result


@pytest.mark.asyncio
async def test_cancellation_produces_structured_report(fake_registry):
    router = MagicMock()
    router.chat = AsyncMock()  # never reached
    cancel_token = MagicMock(is_cancelled=True)
    result = await run_specialist(
        user_id="u1",
        specialist=_SPEC,
        task="do a thing",
        registry=fake_registry,
        eco_router=router,
        permission_checker=None,
        cancel_token=cancel_token,
    )
    assert result.success is False
    assert FAILURE_REPORT_MARKER in result.result
    assert f"outcome: {OUTCOME_CANCELLED}" in result.result


@pytest.mark.asyncio
async def test_all_tools_failed_appends_report_to_final_text(fake_registry):
    """Specialist finishes 'normally' but every tool call errored — the
    final text gets the report APPENDED so the consolidator sees both."""
    router = MagicMock()
    router.chat = AsyncMock(side_effect=[
        _tool_call_response("browser", {"action": "open"}, "tc_1"),
        LLMResponse(
            content="I could not open the page.", tool_calls=None,
            model="test-model",
        ),
    ])
    with patch("lazyclaw.teams.runner.ToolExecutor") as MockExec:
        MockExec.return_value.execute = AsyncMock(
            return_value="Error: net::ERR_CONNECTION_REFUSED",
        )
        result = await run_specialist(
            user_id="u1",
            specialist=_SPEC,
            task="open example.com",
            registry=fake_registry,
            eco_router=router,
            permission_checker=None,
        )

    # Normal completion keeps success=True (no semantics flip for
    # delegate's background_done routing) but carries the report.
    assert result.success is True
    assert result.result.startswith("I could not open the page.")
    assert FAILURE_REPORT_MARKER in result.result
    assert f"outcome: {OUTCOME_ALL_TOOLS_FAILED}" in result.result
    assert "ERR_CONNECTION_REFUSED" in result.result


@pytest.mark.asyncio
async def test_success_path_unchanged_no_report(fake_registry):
    router = MagicMock()
    router.chat = AsyncMock(side_effect=[
        _tool_call_response("browser", {"action": "open"}, "tc_1"),
        LLMResponse(
            content="Page read. The answer is 42.", tool_calls=None,
            model="test-model",
        ),
    ])
    with patch("lazyclaw.teams.runner.ToolExecutor") as MockExec:
        MockExec.return_value.execute = AsyncMock(return_value="page content")
        result = await run_specialist(
            user_id="u1",
            specialist=_SPEC,
            task="open example.com",
            registry=fake_registry,
            eco_router=router,
            permission_checker=None,
        )

    assert result.success is True
    assert result.result == "Page read. The answer is 42."
    assert FAILURE_REPORT_MARKER not in result.result
