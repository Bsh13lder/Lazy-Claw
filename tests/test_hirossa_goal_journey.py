"""End-to-end journey test: full Hirossa-style goal flow through the skills.

Walks the same path the user described in the planning conversation:

  1. start_goal("Sell my product on Hirossa")  →  batch-ask shows 3 inputs
  2. answer_goal_questions(answers)            →  status flips to EXECUTING
  3. mark_step done × 4                         →  steps_done counter ticks
  4. goal_status                                →  shows 4/4 + 'done'
  5. mark_done                                   →  status DONE
  6. goal_progress_report                        →  shows 'no active goals'

LLM + dispatcher are stubbed. The test exercises:
  - registry wires new skills end-to-end
  - encrypted plan_json round-trips
  - skill error paths (unknown goal_id prefix, partial answers)
  - the slim heartbeat regex accepts the GOAL_PROGRESS prefix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime.fix_plan import FixPlan
from lazyclaw.runtime.goal_executor import GoalExecutor, GoalStatus
from lazyclaw.skills.builtin.goal import (
    AbortGoalSkill,
    AnswerGoalQuestionsSkill,
    GoalProgressReportSkill,
    GoalStatusSkill,
    ListGoalsSkill,
    StartGoalSkill,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-hirossa"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.fixture
def stub_planner(monkeypatch):
    """Brain returns a 4-step Hirossa plan with 3 batch-ask questions."""

    async def _no_lazybrain(*a, **k):
        return {"results": [], "source": "empty"}

    async def _no_research(*a, **k):
        return ""

    fake_plan = FixPlan(
        summary="Sell one product on Hirossa: log in, add product, publish.",
        steps=[
            "Open hirossa.com and log into the seller dashboard.",
            "Click 'Add product' and upload the primary image.",
            "Set title, description and price.",
            "Hit publish and verify the live listing URL.",
        ],
        questions=[
            "Which Hirossa account email do you want to use?",
            "What's the product name?",
            "What price should I set?",
        ],
        risks=["payment confirmation requires user approval"],
        confidence="high",
    )

    async def _build_fix_plan(*a, **k):
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


@pytest.mark.asyncio
async def test_hirossa_full_journey(tmp_config, stub_planner):
    start = StartGoalSkill(config=tmp_config)
    answer = AnswerGoalQuestionsSkill(config=tmp_config)
    status = GoalStatusSkill(config=tmp_config)
    listing = ListGoalsSkill(config=tmp_config)
    report = GoalProgressReportSkill(config=tmp_config)

    # 1. Start — batch-ask card surfaces 3 questions in one go.
    out = await start.execute("u1", {"title": "Sell my product on Hirossa"})
    assert "answers needed" in out.lower()
    assert "Which Hirossa account email" in out
    assert "What price should I set" in out

    # Find the goal id from the rendered output ("Goal `<8 hex>` drafted").
    import re
    goal_id_match = re.search(r"`([a-f0-9]{8})`", out)
    assert goal_id_match, f"goal id not in output: {out!r}"
    goal_id_short = goal_id_match.group(1)

    # 2. Partial answer keeps it AWAITING.
    out2 = await answer.execute("u1", {
        "goal_id": goal_id_short,
        "answers": {
            "Which Hirossa account email do you want to use?": "alice@hirossa.com",
        },
    })
    assert "still awaiting" in out2.lower()

    # 3. Full answers → EXECUTING.
    out3 = await answer.execute("u1", {
        "goal_id": goal_id_short,
        "answers": {
            "What's the product name?": "Iberico Linen Throw",
            "What price should I set?": "$48",
        },
    })
    assert "dispatching" in out3.lower()
    assert "executing" in out3.lower()

    # 4. status_block shows EXECUTING + 0/4 steps done.
    out4 = await status.execute("u1", {"goal_id": goal_id_short})
    assert "executing" in out4
    assert "0/4" in out4

    # 5. Mark each of the 4 steps done — exercises mark_step counter.
    executor = GoalExecutor(tmp_config)
    # Resolve full id from prefix the way answer skill does.
    from lazyclaw.skills.builtin.goal.answer_skill import _resolve_goal_id
    from lazyclaw.runtime.goal_executor import GoalRepository
    repo = GoalRepository(tmp_config)
    full_id = await _resolve_goal_id(repo, "u1", goal_id_short)
    assert full_id is not None

    for idx in range(4):
        await executor.mark_step("u1", full_id, idx, "running",
                                 action=f"step {idx} starting")
        await executor.mark_step("u1", full_id, idx, "done",
                                 action=f"step {idx} done")

    # Mark goal done.
    final = await executor.mark_done("u1", full_id)
    assert final.status == GoalStatus.DONE
    assert final.steps_done == 4

    # 6. status_block now shows DONE.
    out5 = await status.execute("u1", {"goal_id": goal_id_short})
    assert "done" in out5
    assert "4/4" in out5

    # 7. progress_report — no active goals left.
    rep = await report.execute("u1", {})
    assert "no active goals" in rep.lower()

    # 8. list_goals (active only) is empty; with terminal flag includes Hirossa.
    active_only = await listing.execute("u1", {})
    assert "no active goals" in active_only.lower()
    with_terminal = await listing.execute("u1", {"include_terminal": True})
    assert "Sell my product on Hirossa" in with_terminal


@pytest.mark.asyncio
async def test_abort_skill_terminates_a_goal(tmp_config, stub_planner):
    start = StartGoalSkill(config=tmp_config)
    abort = AbortGoalSkill(config=tmp_config)
    status = GoalStatusSkill(config=tmp_config)

    out = await start.execute("u1", {"title": "Goal we will abort"})
    import re
    goal_id_short = re.search(r"`([a-f0-9]{8})`", out).group(1)

    aborted = await abort.execute("u1", {"goal_id": goal_id_short})
    assert "aborted" in aborted

    # Re-aborting is a safe no-op.
    again = await abort.execute("u1", {"goal_id": goal_id_short})
    assert "aborted" in again

    # status_block on a terminated goal still works.
    md = await status.execute("u1", {"goal_id": goal_id_short})
    assert "aborted" in md.lower()


@pytest.mark.asyncio
async def test_answer_unknown_goal_id_prefix(tmp_config):
    answer = AnswerGoalQuestionsSkill(config=tmp_config)
    out = await answer.execute("u1", {
        "goal_id": "deadbeef",
        "answers": {"q": "a"},
    })
    assert "no goal matching" in out.lower()


@pytest.mark.asyncio
async def test_progress_report_with_active_goal(tmp_config, stub_planner):
    start = StartGoalSkill(config=tmp_config)
    report = GoalProgressReportSkill(config=tmp_config)

    await start.execute("u1", {"title": "Active hirossa goal"})

    rep = await report.execute("u1", {})
    assert "Daily goal report" in rep
    assert "Active hirossa goal" in rep

    rep_v = await report.execute("u1", {"verbose": True})
    assert "Active hirossa goal" in rep_v
    # Verbose mode includes the question list.
    assert "Pending questions" in rep_v


def test_slim_heartbeat_regex_accepts_goal_progress():
    from lazyclaw.runtime.agent import _SLIM_HEARTBEAT_PREFIX_RE as R
    assert R.match("[GOAL_PROGRESS] all")
    assert R.match("[REMINDER] foo")
    assert not R.match("[JOB:foo] bar")
