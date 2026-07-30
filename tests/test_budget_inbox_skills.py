"""Tests for ListProjectsSkill and ListBudgetTopupsSkill.

Focus: read-only skills for enumerating projects with budget/spent/remaining,
and listing budget top-ups (the money-IN ledger side) with source tracking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import pending as budget_pending
from lazyclaw.budgets import store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.skills.builtin.budget_manager import (
    AddExpenseSkill,
    ExpenseReportSkill,
    ListBudgetTopupsSkill,
    ListExpensesSkill,
    ListProjectsSkill,
    MoveExpenseSkill,
)

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


async def test_list_projects_lists_budget_spent_remaining(cfg):
    await store.create_project(cfg, "u1", "ClubBay", budget=500)
    await store.create_project(cfg, "u1", "Nima", budget=100)

    msg = await ListProjectsSkill(cfg).execute("u1", {})
    assert "ClubBay" in msg and "Nima" in msg
    assert "500" in msg and "100" in msg


async def test_list_budget_topups_shows_ledger(cfg):
    p = await store.create_project(cfg, "u1", "ClubBay", budget=100)
    await store.add_budget_entry(cfg, "u1", p["id"], amount=400, source="client deposit")

    msg = await ListBudgetTopupsSkill(cfg).execute("u1", {"project": "ClubBay"})
    assert "400" in msg and "client deposit" in msg and "ClubBay" in msg


async def test_list_budget_topups_empty(cfg):
    await store.create_project(cfg, "u1", "Nima", budget=50)
    msg = await ListBudgetTopupsSkill(cfg).execute("u1", {})
    assert "no top-ups" in msg.lower()


async def test_list_expenses_cross_project_shows_project_names(cfg):
    a = await store.create_project(cfg, "u1", "ClubBay")
    b = await store.create_project(cfg, "u1", "Nima")
    await store.create_expense(cfg, "u1", a["id"], amount=10, description="merch")
    await store.create_expense(cfg, "u1", b["id"], amount=20, description="ads")

    msg = await ListExpensesSkill(cfg).execute("u1", {})
    assert "ClubBay" in msg and "Nima" in msg
    assert "merch" in msg and "ads" in msg


async def test_list_expenses_inbox_alias(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_expense(cfg, "u1", g["id"], amount=7, description="coffee")
    msg = await ListExpensesSkill(cfg).execute("u1", {"project": "inbox"})
    assert "coffee" in msg


async def test_expense_report_shows_inbox_line(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_expense(cfg, "u1", g["id"], amount=7, description="coffee")
    msg = await ExpenseReportSkill(cfg).execute("u1", {})
    assert "📥 Inbox: 1 unassigned" in msg


async def test_add_expense_fallback_mentions_inbox(cfg):
    msg = await AddExpenseSkill(cfg).execute("u1", {"amount": 3, "description": "gum"})
    assert "📥 Inbox" in msg


async def test_move_expense_from_inbox_to_project(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay", budget=100)
    await store.create_expense(cfg, "u1", g["id"], amount=12, description="coffee beans")

    msg = await MoveExpenseSkill(cfg).execute("u1", {"query": "coffee", "project": "ClubBay"})
    assert "Moved" in msg and "ClubBay" in msg
    club = await store.get_project_by_name(cfg, "u1", "ClubBay")
    moved = await store.list_expenses(cfg, "u1", project_id=club["id"])
    assert len(moved) == 1 and moved[0]["description"] == "coffee beans"
    assert await store.list_expenses(cfg, "u1", project_id=g["id"]) == []


async def test_move_expense_ambiguous_query_asks(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay")
    await store.create_expense(cfg, "u1", g["id"], amount=5, description="coffee small")
    await store.create_expense(cfg, "u1", g["id"], amount=9, description="coffee large")

    msg = await MoveExpenseSkill(cfg).execute("u1", {"query": "coffee", "project": "ClubBay"})
    assert "coffee small" in msg and "coffee large" in msg
    assert "No expense was moved" in msg
    assert len(await store.list_expenses(cfg, "u1", project_id=g["id"])) == 2


async def test_move_expense_all_inbox_bulk(cfg):
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "Nima")
    for d in ("a", "b", "c"):
        await store.create_expense(cfg, "u1", g["id"], amount=1, description=d)

    msg = await MoveExpenseSkill(cfg).execute("u1", {"project": "Nima", "all_inbox": True})
    assert "3" in msg and "Nima" in msg
    assert await store.list_expenses(cfg, "u1", project_id=g["id"]) == []


async def test_move_expense_with_task_attach(cfg):
    from lazyclaw.tasks.store import create_task
    g = await store.create_project(cfg, "u1", "General")
    await store.create_project(cfg, "u1", "ClubBay")
    await create_task(cfg, "u1", "Merchandise", category="ClubBay")
    await store.create_expense(cfg, "u1", g["id"], amount=30, description="tshirts")

    msg = await MoveExpenseSkill(cfg).execute(
        "u1", {"query": "tshirts", "project": "ClubBay", "task_name": "Merch"},
    )
    assert "Merchandise" in msg
    club = await store.get_project_by_name(cfg, "u1", "ClubBay")
    moved = await store.list_expenses(cfg, "u1", project_id=club["id"])
    assert moved[0]["task_id"] is not None
