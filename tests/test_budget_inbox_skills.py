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
