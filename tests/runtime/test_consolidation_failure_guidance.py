"""Consolidation turn re-decision guidance on subagent failure.

When ANY subagent in a brain fan-out group fails (structured
``[SPECIALIST FAILURE REPORT]`` or plain failure), the synthetic
consolidation prompt must carry explicit orchestrator instructions so
the brain decides the next move (re-delegate ONCE / answer from
partials / report the blocker) instead of shipping a false "✅ done"
or a vague apology. A cheap observation-only coherence guard logs a
WARNING when the consolidation draft claims success while every
subagent failed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.config import Config
from lazyclaw.runtime.consolidation_guidance import (
    COHERENCE_LOG_TAG,
    FAILURE_GUIDANCE_HEADER,
    build_failure_guidance,
    draft_claims_success,
)
from lazyclaw.runtime.task_runner import TaskRunner
from lazyclaw.teams.failure_report import (
    OUTCOME_STUCK_LOOP,
    render_failure_report,
)


def _make_runner(tmp_path: Path, *, lane_queue=None) -> TaskRunner:
    runner = TaskRunner.__new__(TaskRunner)
    runner._config = Config(database_dir=tmp_path)
    runner._router = MagicMock()
    runner._registry = MagicMock()
    runner._eco_router = MagicMock()
    runner._permission_checker = None
    runner._default_callback = None
    runner._team_lead = None
    runner._lane_queue = lane_queue
    runner._consolidator_factory = None
    runner._running = {}
    runner._task_users = {}
    runner._task_names = {}
    runner._task_starts = {}
    runner._task_provenance = {}
    runner._task_caller_depth = {}
    runner._task_workspace_dirs = {}
    runner._brain_groups = {}
    runner._fanout_retry_rounds = {}
    return runner


def _failure_report() -> str:
    return render_failure_report(
        specialist="freelance_specialist",
        task="Find new Upwork jobs",
        outcome=OUTCOME_STUCK_LOOP,
        tool_history=["search_tools"] * 4,
        tool_results=["a" * 10, "b" * 60, "c" * 250, "d" * 900],
        detail="Called 'search_tools' 4 times in a row without progress.",
    )


async def _settle_pair(runner, group_id, *, fail_second: bool,
                       second_result: str = "", second_error: str = ""):
    runner.register_subagent_fanout(
        group_id, "u1", ["sa-a", "sa-b"], MagicMock(),
        chat_session_id="sess",
    )
    runner.record_subagent_result(
        "sa-a", name="explore subagent", success=True,
        result="WhatsApp: 3 unread", duration_ms=900,
    )
    runner.record_subagent_result(
        "sa-b", name="freelance subagent", success=not fail_second,
        result=second_result, error=second_error, duration_ms=1200,
    )
    await asyncio.sleep(0.02)  # let create_task(_consolidate) run


# ── Pure helpers ──────────────────────────────────────────────────────


class TestDraftClaimsSuccess:
    def test_checkmark(self):
        assert draft_claims_success("✅ All sorted!") is True

    def test_done_word(self):
        assert draft_claims_success("The job search is done.") is True

    def test_completed(self):
        assert draft_claims_success("Task completed as requested") is True

    def test_negated_claim_not_flagged(self):
        assert draft_claims_success(
            "The search could not be completed.",
        ) is False
        assert draft_claims_success("I wasn't able to finish.") is False
        assert draft_claims_success(
            "The specialist failed to complete the search.",
        ) is False

    def test_plain_blocker_report_not_flagged(self):
        assert draft_claims_success(
            "search_tools kept looping; I need different credentials.",
        ) is False

    def test_empty(self):
        assert draft_claims_success("") is False
        assert draft_claims_success(None) is False  # type: ignore[arg-type]


class TestBuildFailureGuidance:
    def test_redelegate_round_offers_all_three_moves(self):
        text = build_failure_guidance(can_redelegate=True)
        assert text.startswith(FAILURE_GUIDANCE_HEADER)
        assert "delegate" in text
        assert "(a)" in text and "(b)" in text and "(c)" in text
        assert "NEVER claim success" in text

    def test_exhausted_round_forbids_redelegation(self):
        text = build_failure_guidance(can_redelegate=False)
        assert text.startswith(FAILURE_GUIDANCE_HEADER)
        assert "EXHAUSTED" in text
        assert "Do NOT delegate" in text
        assert "(a)" not in text
        assert "NEVER claim success" in text

    def test_kept_compact(self):
        # ~10 lines budget — prompt bloat guard.
        for can in (True, False):
            assert len(build_failure_guidance(
                can_redelegate=can,
            ).splitlines()) <= 12


# ── Consolidation prompt wiring ───────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_report_flows_into_consolidation_prompt(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="blocker reported")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    await _settle_pair(
        runner, "grp-fail", fail_second=True,
        second_result=_failure_report(),
        second_error="Called 'search_tools' 4 times in a row without progress.",
    )

    lane_queue.enqueue.assert_awaited_once()
    synthetic = lane_queue.enqueue.await_args.args[1]
    # The structured report body must be visible to the brain…
    assert "outcome: stuck_loop" in synthetic
    assert "search_tools (x4" in synthetic
    # …with the orchestrator guidance block appended.
    assert FAILURE_GUIDANCE_HEADER in synthetic
    assert "(a)" in synthetic  # round 0 → re-delegation offered
    # Partial success still included.
    assert "WhatsApp: 3 unread" in synthetic


@pytest.mark.asyncio
async def test_plain_failure_without_report_still_gets_guidance(tmp_path):
    """A timeout settles with only error text (no report marker) — the
    guidance must still fire so the brain never claims success."""
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    await _settle_pair(
        runner, "grp-timeout", fail_second=True,
        second_error="Timed out after 120s",
    )

    synthetic = lane_queue.enqueue.await_args.args[1]
    assert "Timed out after 120s" in synthetic
    assert FAILURE_GUIDANCE_HEADER in synthetic


@pytest.mark.asyncio
async def test_success_path_has_no_guidance(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="all good")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    await _settle_pair(
        runner, "grp-ok", fail_second=False,
        second_result="Email: 5 unread",
    )

    synthetic = lane_queue.enqueue.await_args.args[1]
    assert FAILURE_GUIDANCE_HEADER not in synthetic
    assert "WhatsApp: 3 unread" in synthetic
    assert "Email: 5 unread" in synthetic
    # No retry round granted on a clean group.
    assert runner._fanout_retry_rounds == {}


@pytest.mark.asyncio
async def test_report_appended_to_successful_result_survives_truncation(tmp_path):
    """An all_tools_failed report rides at the END of a success=True
    result; the 1500-char preview cap must not silently chop it."""
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    long_text = "I tried everything. " * 200  # ≫ preview cap
    report = render_failure_report(
        specialist="browser_specialist", task="open example.com",
        outcome="all_tools_failed",
        tool_history=["browser"], tool_results=["Error: refused"],
    )
    await _settle_pair(
        runner, "grp-trunc", fail_second=False,
        second_result=long_text + "\n\n" + report,
    )

    synthetic = lane_queue.enqueue.await_args.args[1]
    assert "outcome: all_tools_failed" in synthetic
    assert FAILURE_GUIDANCE_HEADER in synthetic


# ── Retry budget ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_budget_caps_redelegation_at_one_round(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="working on it")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    # Round 0: failure → guidance OFFERS re-delegation, grants a round.
    await _settle_pair(
        runner, "grp-r0", fail_second=True, second_result=_failure_report(),
    )
    first = lane_queue.enqueue.await_args.args[1]
    assert "(a)" in first
    assert "u1" in runner._fanout_retry_rounds

    # The brain re-delegates → a NEW group for the same user inherits
    # retry_round=1 (claimed from the pending grant).
    runner.register_subagent_fanout(
        "grp-r1", "u1", ["sa-c", "sa-d"], MagicMock(),
    )
    assert runner._brain_groups["grp-r1"].retry_round == 1
    # Grant consumed — a third group would start at round 0 again.
    assert "u1" not in runner._fanout_retry_rounds

    # Round 1 fails too → guidance FORBIDS further delegation.
    runner.record_subagent_result(
        "sa-c", name="x", success=True, result="partial",
    )
    runner.record_subagent_result(
        "sa-d", name="y", success=False, result=_failure_report(),
        error="stuck again",
    )
    await asyncio.sleep(0.02)
    second = lane_queue.enqueue.await_args.args[1]
    assert "EXHAUSTED" in second
    assert "(a)" not in second
    # No further round granted.
    assert "u1" not in runner._fanout_retry_rounds


@pytest.mark.asyncio
async def test_retry_round_is_per_user(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)
    runner._fanout_retry_rounds["u1"] = (1, __import__("time").monotonic())

    runner.register_subagent_fanout("grp-other", "u2", ["sa-z"], MagicMock())
    assert runner._brain_groups["grp-other"].retry_round == 0
    # u1's grant untouched.
    assert "u1" in runner._fanout_retry_rounds


def test_claim_retry_round_defensive_without_dict(tmp_path):
    """Legacy __new__-style test fixtures don't set _fanout_retry_rounds;
    the claim helper must degrade to round 0, never AttributeError."""
    runner = TaskRunner.__new__(TaskRunner)
    assert runner._claim_retry_round("u1") == 0


# ── Coherence guard (observation-only) ───────────────────────────────


@pytest.mark.asyncio
async def test_false_success_draft_logs_coherence_warning(tmp_path, caplog):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="✅ Done! Everything completed.")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    runner.register_subagent_fanout(
        "grp-lie", "u1", ["sa-a", "sa-b"], MagicMock(),
    )
    with caplog.at_level(logging.WARNING, logger="lazyclaw.runtime.task_runner"):
        runner.record_subagent_result(
            "sa-a", name="x", success=False, result=_failure_report(),
            error="stuck",
        )
        runner.record_subagent_result(
            "sa-b", name="y", success=False, error="Timed out after 120s",
        )
        await asyncio.sleep(0.02)

    assert any(COHERENCE_LOG_TAG in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_no_warning_when_some_subagent_succeeded(tmp_path, caplog):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(
        return_value="✅ WhatsApp done; job search blocked (stuck loop).",
    )
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    with caplog.at_level(logging.WARNING, logger="lazyclaw.runtime.task_runner"):
        await _settle_pair(
            runner, "grp-mixed", fail_second=True,
            second_result=_failure_report(), second_error="stuck",
        )

    assert not any(COHERENCE_LOG_TAG in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_no_warning_on_honest_blocker_draft(tmp_path, caplog):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(
        return_value="Both lookups failed — search_tools looped without "
                     "progress. I could not complete the search.",
    )
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    with caplog.at_level(logging.WARNING, logger="lazyclaw.runtime.task_runner"):
        runner.register_subagent_fanout(
            "grp-honest", "u1", ["sa-a", "sa-b"], MagicMock(),
        )
        runner.record_subagent_result(
            "sa-a", name="x", success=False, error="stuck",
        )
        runner.record_subagent_result(
            "sa-b", name="y", success=False, error="Timed out",
        )
        await asyncio.sleep(0.02)

    assert not any(COHERENCE_LOG_TAG in r.message for r in caplog.records)
