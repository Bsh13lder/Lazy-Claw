"""Tests for AddExpenseSkill task-attachment resolution.

Focus: when an expense names a task that can't be matched, the skill must ASK
BACK (stash a pending kind="task" choice listing the project's tasks) instead
of silently dropping the attachment — unless the project has no tasks at all,
in which case it logs on the project and says so. Mirrors the cfg fixture in
test_budget_store.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import pending as budget_pending
from lazyclaw.budgets import store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.skills.builtin.budget_manager import AddExpenseSkill
from lazyclaw.tasks.store import create_task

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    budget_pending.clear_pending("u1")
    try:
        yield c
    finally:
        budget_pending.clear_pending("u1")
        await close_pool()


async def _spent(cfg: Config) -> float:
    projects = await store.list_projects(cfg, "u1")
    return sum(float(p.get("spent", 0)) for p in projects)


async def test_unmatched_task_asks_back_with_candidates(cfg):
    """Task name given but no match, project HAS tasks → ask back, log nothing."""
    await store.create_project(cfg, "u1", "ClubBay", budget=500)
    await create_task(cfg, "u1", "Merchandise", category="ClubBay")
    await create_task(cfg, "u1", "Venue deposit", category="ClubBay")

    skill = AddExpenseSkill(cfg)
    msg = await skill.execute("u1", {
        "amount": 12, "description": "chino",
        "project": "ClubBay", "task_name": "totally-not-a-real-task",
    })

    # Asks which task, surfacing the real candidates.
    assert "No task on" in msg and "ClubBay" in msg
    assert "Merchandise" in msg and "Venue deposit" in msg
    assert "No expense logged yet" in msg

    # A pending task-choice is stashed so a Telegram tap can resolve it.
    pend = budget_pending.get_pending("u1")
    assert pend is not None
    assert pend.kind == "task"
    assert pend.project_name == "ClubBay"
    cand_titles = {title for _id, title in pend.candidates}
    assert {"Merchandise", "Venue deposit"} <= cand_titles

    # Crucially: nothing was logged.
    assert await _spent(cfg) == 0.0


async def test_matched_task_attaches_and_logs(cfg):
    """Fuzzy/substring task match attaches silently and logs the expense."""
    await store.create_project(cfg, "u1", "ClubBay", budget=500)
    await create_task(cfg, "u1", "Merchandise", category="ClubBay")

    skill = AddExpenseSkill(cfg)
    msg = await skill.execute("u1", {
        "amount": 12, "description": "chino",
        "project": "ClubBay", "task_name": "merch",  # substring of Merchandise
    })

    assert "task" in msg and "Merchandise" in msg
    assert await _spent(cfg) == 12.0
    # No clarification pending left behind after a clean log.
    assert budget_pending.get_pending("u1") is None


async def test_unmatched_task_no_tasks_logs_on_project(cfg):
    """Task name given but project has NO tasks → log on project, say so."""
    await store.create_project(cfg, "u1", "ClubBay", budget=500)

    skill = AddExpenseSkill(cfg)
    msg = await skill.execute("u1", {
        "amount": 12, "description": "chino",
        "project": "ClubBay", "task_name": "merch",
    })

    assert "no tasks on" in msg.lower()
    assert await _spent(cfg) == 12.0
    # Nothing to disambiguate → no pending choice.
    assert budget_pending.get_pending("u1") is None
