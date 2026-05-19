"""P1 — Unified Code Session per project.

Tests that:
  - Goal.code_session_id round-trips through the encrypted DB.
  - GoalExecutor.set_code_session_id persists the id (idempotent).
  - GoalExecutor.continue_code rejects terminal + non-code goals.
  - continue_code on EXECUTING dispatches via the registered ``code``
    slug callback AND stashes the new instruction in the side-channel
    so the dispatch handler picks it up.
  - _compose_code_instruction in continuation mode returns the slim
    "new instruction only" payload (no plan replay).

The runner-level session_id wiring + on_session_id callback is exercised
by `test_code_specialist_capture.py` once the worker is mocked there;
this file is the goal-side persistence + continuation contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    InvalidGoalTransition,
    _CONTINUATION_INSTRUCTIONS,
    pop_continuation_instruction,
    register_default_dispatch,
)


# ── Fixtures ─────────────────────────────────────────────────────────


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
def code_dispatch(monkeypatch):
    """Replace the ``code`` slug dispatcher with a tracer.

    Restores any pre-registered callback on teardown so this file
    doesn't pollute other tests that import the real one.
    """
    from lazyclaw.runtime.goal_executor import (
        _DEFAULT_DISPATCH_BY_SLUG,
    )
    saved = _DEFAULT_DISPATCH_BY_SLUG.get("code")
    trace = _DispatchTrace(calls=[])

    async def _capture(goal: Goal) -> None:
        trace.calls.append(goal)

    register_default_dispatch("code", _capture)
    try:
        yield trace
    finally:
        if saved is not None:
            _DEFAULT_DISPATCH_BY_SLUG["code"] = saved
        else:
            _DEFAULT_DISPATCH_BY_SLUG.pop("code", None)
        _CONTINUATION_INSTRUCTIONS.clear()


@pytest.fixture
def stub_brain_no_questions(monkeypatch):
    """Brain returns a no-questions plan so start() lands in EXECUTING."""

    async def _no_lazybrain(*args, **kwargs):
        return {"results": [], "source": "empty"}

    async def _no_research(*args, **kwargs):
        return ""

    plan = FixPlan(
        summary="Build the BPO bot",
        steps=["scaffold project", "wire CDP backend", "ship watcher"],
        questions=[],
        risks=[],
        confidence="high",
    )

    async def _build_fix_plan(*args, **kwargs):
        return plan

    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search", _no_lazybrain,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.plan_research.gather_plan_research", _no_research,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.fix_plan.build_fix_plan", _build_fix_plan,
    )
    return plan


# ── Goal model round-trip ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_code_session_id_round_trips(tmp_config):
    """Goal.code_session_id survives a write+read cycle."""
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-roundtrip",
        user_id="u1",
        title="scaffold thing",
        status=GoalStatus.EXECUTING,
        work_type="code",
        code_session_id="session-abc-123",
    )
    await repo.create(goal)
    loaded = await repo.get("u1", "g-roundtrip")
    assert loaded is not None
    assert loaded.code_session_id == "session-abc-123"
    assert loaded.work_type == "code"
    assert loaded.status == GoalStatus.EXECUTING


@pytest.mark.asyncio
async def test_set_code_session_id_persists_and_is_idempotent(tmp_config):
    """set_code_session_id writes once; repeat calls with same id no-op."""
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-setter",
        user_id="u1",
        title="x",
        status=GoalStatus.EXECUTING,
        work_type="code",
    )
    await repo.create(goal)

    executor = GoalExecutor(tmp_config, repo)
    await executor.set_code_session_id("u1", "g-setter", "sid-1")
    loaded = await repo.get("u1", "g-setter")
    assert loaded.code_session_id == "sid-1"

    # Idempotent — second call with same id is a no-op
    again = await executor.set_code_session_id("u1", "g-setter", "sid-1")
    assert again.code_session_id == "sid-1"

    # Different id overwrites
    await executor.set_code_session_id("u1", "g-setter", "sid-2")
    loaded = await repo.get("u1", "g-setter")
    assert loaded.code_session_id == "sid-2"


# ── continue_code guards ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_continue_code_rejects_terminal_goal(tmp_config):
    """Cannot continue a DONE / FAILED / ABORTED goal."""
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-done",
        user_id="u1",
        title="x",
        status=GoalStatus.DONE,
        work_type="code",
    )
    await repo.create(goal)
    executor = GoalExecutor(tmp_config, repo)
    with pytest.raises(InvalidGoalTransition):
        await executor.continue_code("u1", "g-done", "now add tests")


@pytest.mark.asyncio
async def test_continue_code_rejects_non_code_goal(tmp_config):
    """Cannot continue a browser/research/etc goal."""
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-browser",
        user_id="u1",
        title="watch a site",
        status=GoalStatus.EXECUTING,
        work_type="web_monitoring",
    )
    await repo.create(goal)
    executor = GoalExecutor(tmp_config, repo)
    with pytest.raises(InvalidGoalTransition):
        await executor.continue_code("u1", "g-browser", "x")


@pytest.mark.asyncio
async def test_continue_code_requires_instruction(tmp_config):
    """Empty/whitespace instruction is a ValueError."""
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-empty",
        user_id="u1",
        title="x",
        status=GoalStatus.EXECUTING,
        work_type="code",
    )
    await repo.create(goal)
    executor = GoalExecutor(tmp_config, repo)
    with pytest.raises(ValueError):
        await executor.continue_code("u1", "g-empty", "   ")


# ── continue_code happy path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_continue_code_dispatches_and_stashes_instruction(
    tmp_config, code_dispatch,
):
    """EXECUTING + code → fires registered 'code' dispatcher with the goal.

    The new turn instruction is stashed in the side-channel so the real
    code_goal_executor.dispatch_code_goal can pop it on entry.
    """
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-cont",
        user_id="u1",
        title="scaffold estreet-bot",
        status=GoalStatus.EXECUTING,
        work_type="code",
        code_session_id="prior-session-xyz",
    )
    await repo.create(goal)

    executor = GoalExecutor(tmp_config, repo)
    returned = await executor.continue_code(
        "u1", "g-cont", "now add a city filter for Oakland",
    )

    # Dispatcher fired once with the goal
    assert len(code_dispatch.calls) == 1
    assert code_dispatch.calls[0].id == "g-cont"

    # Side-channel was set BEFORE the dispatch ran (then popped by the
    # fake dispatcher? — no, our fake doesn't pop. So it should still be
    # popped by us here to verify it landed.)
    # Note: real dispatch_code_goal calls pop_continuation_instruction;
    # our trace fixture does NOT, so we can read it back.
    stashed = pop_continuation_instruction("g-cont")
    assert stashed == "now add a city filter for Oakland"

    # Goal got a progress bump
    assert returned.last_action.startswith("continued:")
    # code_session_id is preserved (not cleared) so the next dispatch resumes
    assert returned.code_session_id == "prior-session-xyz"


@pytest.mark.asyncio
async def test_continue_code_unblocks_a_blocked_goal(
    tmp_config, code_dispatch,
):
    """A BLOCKED code goal transitions back to EXECUTING on continue_code."""
    repo = GoalRepository(tmp_config)
    goal = Goal(
        id="g-blocked",
        user_id="u1",
        title="x",
        status=GoalStatus.BLOCKED,
        work_type="code_project",
        blocked_on="missing creds",
        code_session_id="sid-9",
    )
    await repo.create(goal)
    executor = GoalExecutor(tmp_config, repo)
    returned = await executor.continue_code(
        "u1", "g-blocked", "creds added, please continue",
    )
    assert returned.status == GoalStatus.EXECUTING
    assert returned.blocked_on is None
    assert len(code_dispatch.calls) == 1


# ── Compose instruction shapes ───────────────────────────────────────


def test_compose_instruction_fresh_includes_full_brief():
    """Fresh dispatch → full brief (title + summary + steps + Q/A + risks)."""
    from lazyclaw.runtime.code_goal_executor import _compose_code_instruction
    from lazyclaw.runtime.goal_executor import GoalStep

    goal = Goal(
        id="g-x",
        user_id="u1",
        title="Build a BPO bot",
        status=GoalStatus.EXECUTING,
        work_type="code",
        summary="Real-time eStreet AMC monitor with 1-tap accept.",
        plan=(
            GoalStep(idx=0, description="scaffold the project"),
            GoalStep(idx=1, description="wire CDP backend"),
        ),
        answers={"Target cities?": "Oakland, Hayward, San Leandro"},
        risks=("Cloudflare may flag fresh fingerprint",),
    )
    out = _compose_code_instruction(goal)
    assert "# Build request" in out
    assert "Build a BPO bot" in out
    assert "Real-time eStreet AMC monitor" in out
    assert "scaffold the project" in out
    assert "Oakland" in out
    assert "Cloudflare" in out
    assert "claude-code MCP" in out


def test_compose_instruction_continuation_is_slim():
    """Continuation → ONLY the new instruction + 1-line resume note.

    Crucial: no plan replay, no Q/A replay. The worker already has them
    in its --resume'd session.
    """
    from lazyclaw.runtime.code_goal_executor import _compose_code_instruction
    from lazyclaw.runtime.goal_executor import GoalStep

    goal = Goal(
        id="g-y",
        user_id="u1",
        title="Build a BPO bot",
        status=GoalStatus.EXECUTING,
        work_type="code",
        summary="Real-time eStreet AMC monitor",
        plan=(GoalStep(idx=0, description="scaffold the project"),),
        answers={"q?": "a"},
    )
    out = _compose_code_instruction(
        goal, additional_instruction="now add city filter for Oakland",
    )
    assert "Continuing goal" in out
    assert "now add city filter for Oakland" in out
    # MUST NOT replay the original brief
    assert "scaffold the project" not in out
    assert "Real-time eStreet AMC monitor" not in out
    assert "q?" not in out
    # Should be short — a few hundred chars, not the full brief
    assert len(out) < 600


# ── pop_continuation_instruction semantics ──────────────────────────


def test_pop_continuation_instruction_is_one_shot():
    """Popping the side-channel removes the entry — second pop is None."""
    _CONTINUATION_INSTRUCTIONS["g-pop"] = "do thing"
    assert pop_continuation_instruction("g-pop") == "do thing"
    assert pop_continuation_instruction("g-pop") is None
