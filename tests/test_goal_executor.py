"""End-to-end test of GoalExecutor against a real (tmp) DB.

Brain LLM and dispatcher are mocked. Exercises:
  - start() with questions → AWAITING_USER_INFO + batch-ask payload
  - submit_answers() partial → still AWAITING_USER_INFO
  - submit_answers() full → EXECUTING + dispatch invoked once
  - mark_step / mark_done / mark_blocked / abort transitions
  - status_block markdown for one goal and the digest view
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime.fix_plan import FixPlan
from lazyclaw.runtime.goal_executor import (
    Goal,
    GoalExecutor,
    GoalRepository,
    GoalStatus,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@dataclass
class _DispatchTrace:
    calls: list[Goal]


@pytest.fixture
def dispatch_trace():
    return _DispatchTrace(calls=[])


@pytest.fixture
def stub_brain(monkeypatch):
    """Replace the LLM-side helpers so tests are deterministic + fast."""

    async def _no_lazybrain(*args, **kwargs):
        return {"results": [], "source": "empty"}

    async def _no_research(*args, **kwargs):
        return ""

    fake_plan = FixPlan(
        summary="Add a single product on Hirossa and publish it.",
        steps=[
            "Open hirossa.com and log into the seller dashboard.",
            "Click 'Add product', upload primary image, set title/price.",
            "Hit publish and verify the live listing URL.",
        ],
        questions=["Which Hirossa account email?", "What price tier?"],
        risks=["payment confirmation needs user approval"],
        confidence="medium",
    )

    async def _build_fix_plan(*args, **kwargs):
        return fake_plan

    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search", _no_lazybrain,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.plan_research.gather_plan_research", _no_research,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.fix_plan.build_fix_plan", _build_fix_plan,
    )
    return fake_plan


@pytest.fixture
def stub_brain_no_questions(monkeypatch):
    """Variant: brain returns a plan with NO questions → autostart EXECUTING."""

    async def _no_lazybrain(*args, **kwargs):
        return {"results": [], "source": "empty"}

    async def _no_research(*args, **kwargs):
        return ""

    fake_plan = FixPlan(
        summary="Add product (no clarifying questions)",
        steps=["step a", "step b"],
        questions=[],
        risks=[],
        confidence="high",
    )

    async def _build_fix_plan(*args, **kwargs):
        return fake_plan

    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search", _no_lazybrain,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.plan_research.gather_plan_research", _no_research,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.fix_plan.build_fix_plan", _build_fix_plan,
    )
    return fake_plan


# ── start() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_with_questions_lands_in_awaiting(
    tmp_config, stub_brain, dispatch_trace,
):
    async def dispatch(goal: Goal) -> None:
        dispatch_trace.calls.append(goal)

    executor = GoalExecutor(tmp_config, dispatch_callback=dispatch)
    goal = await executor.start("u1", "Sell my product on Hirossa")

    assert goal.status == GoalStatus.AWAITING_USER_INFO
    assert len(goal.plan) == 3
    assert list(goal.questions_pending) == [
        "Which Hirossa account email?", "What price tier?",
    ]
    assert goal.confidence == "medium"
    # Dispatch must NOT fire yet — answers still pending.
    assert dispatch_trace.calls == []


@pytest.mark.asyncio
async def test_start_with_no_questions_dispatches_immediately(
    tmp_config, stub_brain_no_questions, dispatch_trace,
):
    async def dispatch(goal: Goal) -> None:
        dispatch_trace.calls.append(goal)

    executor = GoalExecutor(tmp_config, dispatch_callback=dispatch)
    goal = await executor.start("u1", "Quick browser task")

    assert goal.status == GoalStatus.EXECUTING
    assert len(dispatch_trace.calls) == 1
    assert dispatch_trace.calls[0].id == goal.id


@pytest.mark.asyncio
async def test_start_persists_goal_with_encrypted_title(
    tmp_config, stub_brain,
):
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Sell my product on Hirossa")

    # Reload from DB — exercises full encrypt/decrypt round-trip.
    repo = GoalRepository(tmp_config)
    reloaded = await repo.get("u1", goal.id)
    assert reloaded is not None
    assert reloaded.title == "Sell my product on Hirossa"
    assert reloaded.confidence == "medium"
    assert len(reloaded.plan) == 3


@pytest.mark.asyncio
async def test_start_rejects_empty_title(tmp_config, stub_brain):
    executor = GoalExecutor(tmp_config)
    with pytest.raises(ValueError):
        await executor.start("u1", "   ")


# ── submit_answers() ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_answers_keep_status_awaiting(
    tmp_config, stub_brain, dispatch_trace,
):
    async def dispatch(goal: Goal) -> None:
        dispatch_trace.calls.append(goal)

    executor = GoalExecutor(tmp_config, dispatch_callback=dispatch)
    goal = await executor.start("u1", "Sell my product on Hirossa")

    updated = await executor.submit_answers("u1", goal.id, {
        "Which Hirossa account email?": "alice@hirossa.com",
    })
    assert updated.status == GoalStatus.AWAITING_USER_INFO
    assert "What price tier?" in updated.questions_pending
    assert "Which Hirossa account email?" in updated.answers
    assert dispatch_trace.calls == []


@pytest.mark.asyncio
async def test_full_answers_transition_to_executing_and_dispatch(
    tmp_config, stub_brain, dispatch_trace,
):
    async def dispatch(goal: Goal) -> None:
        dispatch_trace.calls.append(goal)

    executor = GoalExecutor(tmp_config, dispatch_callback=dispatch)
    goal = await executor.start("u1", "Sell my product on Hirossa")

    updated = await executor.submit_answers("u1", goal.id, {
        "Which Hirossa account email?": "alice@hirossa.com",
        "What price tier?": "$29",
    })

    assert updated.status == GoalStatus.EXECUTING
    assert updated.questions_pending == ()
    assert len(dispatch_trace.calls) == 1
    assert dispatch_trace.calls[0].id == goal.id


@pytest.mark.asyncio
async def test_submit_answers_unknown_goal_raises(tmp_config, stub_brain):
    executor = GoalExecutor(tmp_config)
    with pytest.raises(LookupError):
        await executor.submit_answers("u1", "no-such-goal", {"x": "y"})


@pytest.mark.asyncio
async def test_submit_answers_in_wrong_status_raises(
    tmp_config, stub_brain_no_questions,
):
    # No-questions plan jumps straight to EXECUTING — submit_answers
    # then is invalid.
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Quick task")
    from lazyclaw.runtime.goal_executor import InvalidGoalTransition
    with pytest.raises(InvalidGoalTransition):
        await executor.submit_answers("u1", goal.id, {"q": "a"})


# ── Outcome handlers ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_step_running_then_done_updates_counters(
    tmp_config, stub_brain_no_questions,
):
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Quick task")

    g1 = await executor.mark_step("u1", goal.id, 0, "running", action="opened browser")
    assert g1.plan[0].status == "running"
    assert g1.plan[0].started_at is not None
    assert g1.steps_done == 0

    g2 = await executor.mark_step("u1", goal.id, 0, "done", action="step a complete")
    assert g2.plan[0].status == "done"
    assert g2.plan[0].completed_at is not None
    assert g2.steps_done == 1


@pytest.mark.asyncio
async def test_mark_done_terminal(
    tmp_config, stub_brain_no_questions,
):
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Quick task")
    done = await executor.mark_done("u1", goal.id)
    assert done.status == GoalStatus.DONE


@pytest.mark.asyncio
async def test_mark_blocked_records_reason(
    tmp_config, stub_brain_no_questions,
):
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Quick task")
    blocked = await executor.mark_blocked("u1", goal.id, "site is down")
    assert blocked.status == GoalStatus.BLOCKED
    assert blocked.blocked_on == "site is down"


@pytest.mark.asyncio
async def test_abort_is_terminal(
    tmp_config, stub_brain_no_questions,
):
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Quick task")
    aborted = await executor.abort("u1", goal.id)
    assert aborted.status == GoalStatus.ABORTED

    # Already terminal — calling abort again is a no-op (no exception).
    again = await executor.abort("u1", goal.id)
    assert again.status == GoalStatus.ABORTED


# ── Repository ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repository_list_filters_by_status(
    tmp_config, stub_brain,
):
    executor = GoalExecutor(tmp_config)
    a = await executor.start("u1", "goal a")
    b = await executor.start("u1", "goal b")
    await executor.abort("u1", b.id)

    repo = GoalRepository(tmp_config)
    awaiting = await repo.list("u1", statuses=(GoalStatus.AWAITING_USER_INFO,))
    aborted = await repo.list("u1", statuses=(GoalStatus.ABORTED,))
    assert {g.id for g in awaiting} == {a.id}
    assert {g.id for g in aborted} == {b.id}


@pytest.mark.asyncio
async def test_repository_get_returns_none_for_missing(tmp_config):
    repo = GoalRepository(tmp_config)
    assert await repo.get("u1", "no-such-id") is None


# ── status_block ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_block_per_goal_renders_steps_and_questions(
    tmp_config, stub_brain,
):
    executor = GoalExecutor(tmp_config)
    goal = await executor.start("u1", "Sell my product on Hirossa")
    md = await executor.status_block("u1", goal.id)
    assert "Sell my product on Hirossa" in md
    assert "awaiting_user_info" in md
    assert "Open hirossa.com" in md
    assert "Which Hirossa account email?" in md


@pytest.mark.asyncio
async def test_status_block_digest_lists_only_active(
    tmp_config, stub_brain,
):
    executor = GoalExecutor(tmp_config)
    a = await executor.start("u1", "active goal")
    b = await executor.start("u1", "to be aborted")
    await executor.abort("u1", b.id)

    md = await executor.status_block("u1")
    assert "active goal" in md
    assert "to be aborted" not in md  # aborted not in active digest


@pytest.mark.asyncio
async def test_status_block_unknown_goal_returns_friendly_message(
    tmp_config,
):
    executor = GoalExecutor(tmp_config)
    md = await executor.status_block("u1", "no-such-goal")
    assert "not found" in md.lower()


@pytest.mark.asyncio
async def test_status_block_no_active_goals(tmp_config):
    executor = GoalExecutor(tmp_config)
    md = await executor.status_block("u1")
    assert "no active goals" in md.lower()
