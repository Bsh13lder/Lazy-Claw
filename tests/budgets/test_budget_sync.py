"""Offline-sync primitives for the Budgets domain (projects + expenses).

Covers four requirements:
1. Client-supplied id on create is honoured; duplicate create is idempotent.
2. Soft-delete (deleted_at): hides from list/get but surfaces in /changes.
3. GET /api/budgets/changes?since=<iso>: delta filtering including tombstones.
4. Soft-delete cascade: deleting a project also soft-deletes its expenses.

NOTE: projects and project_expenses already have updated_at in schema —
      we do NOT re-add it, just test that it is set/bumped correctly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.budgets import store as budget_store

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_project(cfg, name="Test Project", budget=100.0) -> dict:
    return await budget_store.create_project(
        cfg, "u1", name, budget=budget, currency="EUR",
    )


async def _make_expense(cfg, project_id: str, amount=10.0) -> dict:
    return await budget_store.create_expense(
        cfg, "u1", project_id, amount=amount, currency="EUR",
    )


# ---------------------------------------------------------------------------
# 1. Client-supplied id on create (idempotent replay) — projects
# ---------------------------------------------------------------------------


async def test_create_project_with_client_id(cfg):
    """create_project with project_id= uses the client-supplied id."""
    client_id = "client-project-id-abc123"
    project = await budget_store.create_project(
        cfg, "u1", "My Project", budget=200.0, project_id=client_id,
    )
    assert project["id"] == client_id


async def test_create_project_with_client_id_idempotent(cfg):
    """Second create with the same id returns the existing project (no duplicate)."""
    client_id = "idem-project-xyz789"
    first = await budget_store.create_project(
        cfg, "u1", "Idempotent Project", budget=50.0, project_id=client_id,
    )
    second = await budget_store.create_project(
        cfg, "u1", "Idempotent Project", budget=50.0, project_id=client_id,
    )
    assert first["id"] == client_id
    assert second["id"] == client_id, "duplicate client-id must return the same project"

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM projects WHERE id = ? AND user_id = 'u1'",
            (client_id,),
        )
        row = await cur.fetchone()
    assert row[0] == 1, "idempotent create must not insert duplicate rows"


async def test_create_project_without_client_id_generates_uuid(cfg):
    """Without project_id=, a fresh UUID is generated."""
    project = await _make_project(cfg)
    assert len(project["id"]) == 36, "auto-generated id should be a UUID"


# ---------------------------------------------------------------------------
# 2. Client-supplied id on create (idempotent replay) — expenses
# ---------------------------------------------------------------------------


async def test_create_expense_with_client_id(cfg):
    """create_expense with expense_id= uses the client-supplied id."""
    project = await _make_project(cfg)
    client_id = "client-expense-id-abc123"
    expense = await budget_store.create_expense(
        cfg, "u1", project["id"], amount=25.0, expense_id=client_id,
    )
    assert expense["id"] == client_id


async def test_create_expense_with_client_id_idempotent(cfg):
    """Second create with the same expense id returns existing (no duplicate)."""
    project = await _make_project(cfg)
    client_id = "idem-expense-xyz789"
    first = await budget_store.create_expense(
        cfg, "u1", project["id"], amount=25.0, expense_id=client_id,
    )
    second = await budget_store.create_expense(
        cfg, "u1", project["id"], amount=25.0, expense_id=client_id,
    )
    assert first["id"] == client_id
    assert second["id"] == client_id

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM project_expenses WHERE id = ? AND user_id = 'u1'",
            (client_id,),
        )
        row = await cur.fetchone()
    assert row[0] == 1, "idempotent expense create must not insert duplicate rows"


async def test_create_expense_without_client_id_generates_uuid(cfg):
    """Without expense_id=, a fresh UUID is generated."""
    project = await _make_project(cfg)
    expense = await _make_expense(cfg, project["id"])
    assert len(expense["id"]) == 36


# ---------------------------------------------------------------------------
# 3. Soft-delete projects: hides from reads, row preserved, expense cascade
# ---------------------------------------------------------------------------


async def test_delete_project_sets_deleted_at(cfg):
    """delete_project sets deleted_at instead of removing the row."""
    project = await _make_project(cfg)
    ok = await budget_store.delete_project(cfg, "u1", project["id"], cascade=True)
    assert ok is True

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM projects WHERE id = ? AND user_id = 'u1'",
            (project["id"],),
        )
        row = await cur.fetchone()
    assert row is not None, "row must still exist after soft-delete"
    assert row[0] is not None, "deleted_at must be set after delete_project"


async def test_get_project_returns_none_after_soft_delete(cfg):
    """get_project hides soft-deleted projects."""
    project = await _make_project(cfg)
    await budget_store.delete_project(cfg, "u1", project["id"], cascade=True)
    result = await budget_store.get_project(cfg, "u1", project["id"])
    assert result is None, "get_project must not return soft-deleted projects"


async def test_list_projects_excludes_soft_deleted(cfg):
    """list_projects excludes soft-deleted projects."""
    p_keep = await _make_project(cfg, "Keep Me")
    p_del = await _make_project(cfg, "Delete Me")
    await budget_store.delete_project(cfg, "u1", p_del["id"], cascade=True)

    projects = await budget_store.list_projects(cfg, "u1")
    ids = [p["id"] for p in projects]
    assert p_keep["id"] in ids
    assert p_del["id"] not in ids


async def test_delete_project_also_soft_deletes_expenses(cfg):
    """Soft-deleting a project soft-deletes its expenses too."""
    project = await _make_project(cfg)
    expense = await _make_expense(cfg, project["id"])
    await budget_store.delete_project(cfg, "u1", project["id"], cascade=True)

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM project_expenses WHERE id = ? AND user_id = 'u1'",
            (expense["id"],),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None, "expense must be soft-deleted when project is soft-deleted"


async def test_delete_project_returns_false_for_missing_project(cfg):
    """delete_project returns False if the project doesn't exist."""
    result = await budget_store.delete_project(cfg, "u1", "nonexistent-id")
    assert result is False


async def test_delete_project_refuses_without_cascade_when_has_expenses(cfg):
    """delete_project returns False when project has expenses and cascade=False."""
    project = await _make_project(cfg)
    await _make_expense(cfg, project["id"])

    # Should still refuse (or return False) when there are live expenses and cascade=False
    # The soft-delete version should behave like the old version for this guard
    ok = await budget_store.delete_project(cfg, "u1", project["id"], cascade=False)
    assert ok is False


# ---------------------------------------------------------------------------
# 4. Soft-delete expenses: hides from reads, row preserved
# ---------------------------------------------------------------------------


async def test_delete_expense_sets_deleted_at(cfg):
    """delete_expense sets deleted_at instead of removing the row."""
    project = await _make_project(cfg)
    expense = await _make_expense(cfg, project["id"])
    ok = await budget_store.delete_expense(cfg, "u1", expense["id"])
    assert ok is True

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM project_expenses WHERE id = ? AND user_id = 'u1'",
            (expense["id"],),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None, "deleted_at must be set after delete_expense"


async def test_list_expenses_excludes_soft_deleted(cfg):
    """list_expenses excludes soft-deleted expenses."""
    project = await _make_project(cfg)
    e_keep = await _make_expense(cfg, project["id"])
    e_del = await _make_expense(cfg, project["id"])
    await budget_store.delete_expense(cfg, "u1", e_del["id"])

    expenses = await budget_store.list_expenses(cfg, "u1", project_id=project["id"])
    ids = [e["id"] for e in expenses]
    assert e_keep["id"] in ids
    assert e_del["id"] not in ids


async def test_delete_expense_returns_false_for_missing_expense(cfg):
    """delete_expense returns False if the expense doesn't exist."""
    result = await budget_store.delete_expense(cfg, "u1", "nonexistent-expense-id")
    assert result is False


# ---------------------------------------------------------------------------
# 5. GET /api/budgets/changes?since=<iso> — delta feed
# ---------------------------------------------------------------------------


async def test_get_budget_changes_returns_all_when_no_since(cfg):
    """Without ?since=, all live + tombstoned rows are in the response."""
    p1 = await _make_project(cfg, "Project Alpha")
    p2 = await _make_project(cfg, "Project Beta")
    await budget_store.delete_project(cfg, "u1", p2["id"], cascade=True)

    result = await budget_store.get_budget_changes(cfg, "u1", since=None)
    project_ids = [p["id"] for p in result["projects"]]
    assert p1["id"] in project_ids
    assert p2["id"] in result["deleted_projects"]
    assert "now" in result


async def test_get_budget_changes_now_field_is_valid_iso(cfg):
    """The `now` field in the response is a valid ISO datetime."""
    result = await budget_store.get_budget_changes(cfg, "u1", since=None)
    dt = datetime.fromisoformat(result["now"])
    assert dt is not None


async def test_get_budget_changes_filters_projects_by_since(cfg):
    """Only projects updated after `since` are returned."""
    import asyncio

    p_before = await _make_project(cfg, "Before Project")
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    p_after = await _make_project(cfg, "After Project")

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    project_ids = [p["id"] for p in result["projects"]]
    assert p_after["id"] in project_ids, "project created after since must appear"
    assert p_before["id"] not in project_ids, "project created before since must be excluded"


async def test_get_budget_changes_includes_deleted_projects_after_since(cfg):
    """Projects soft-deleted after `since` appear in deleted_projects."""
    import asyncio

    project = await _make_project(cfg, "Will Be Deleted")
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    await budget_store.delete_project(cfg, "u1", project["id"], cascade=True)

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    assert project["id"] in result["deleted_projects"]


async def test_get_budget_changes_excludes_old_deletes_before_since(cfg):
    """Soft-deletes that happened before `since` are not in deleted_projects."""
    import asyncio

    project = await _make_project(cfg, "Old Delete")
    await budget_store.delete_project(cfg, "u1", project["id"], cascade=True)
    await asyncio.sleep(0.02)
    checkpoint = datetime.now(timezone.utc).isoformat()

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    assert project["id"] not in result["deleted_projects"]


async def test_get_budget_changes_filters_expenses_by_since(cfg):
    """Only expenses updated after `since` are returned."""
    import asyncio

    project = await _make_project(cfg)
    e_before = await _make_expense(cfg, project["id"])
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    e_after = await _make_expense(cfg, project["id"])

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    expense_ids = [e["id"] for e in result["expenses"]]
    assert e_after["id"] in expense_ids
    assert e_before["id"] not in expense_ids


async def test_get_budget_changes_includes_deleted_expenses_after_since(cfg):
    """Expenses soft-deleted after `since` appear in deleted_expenses."""
    import asyncio

    project = await _make_project(cfg)
    expense = await _make_expense(cfg, project["id"])
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    await budget_store.delete_expense(cfg, "u1", expense["id"])

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    assert expense["id"] in result["deleted_expenses"]


async def test_get_budget_changes_excludes_old_expense_deletes_before_since(cfg):
    """Expense soft-deletes that happened before `since` are not in deleted_expenses."""
    import asyncio

    project = await _make_project(cfg)
    expense = await _make_expense(cfg, project["id"])
    await budget_store.delete_expense(cfg, "u1", expense["id"])
    await asyncio.sleep(0.02)
    checkpoint = datetime.now(timezone.utc).isoformat()

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    assert expense["id"] not in result["deleted_expenses"]


async def test_get_budget_changes_response_shape(cfg):
    """Response always has all four keys: projects, expenses, deleted_projects, deleted_expenses, now."""
    result = await budget_store.get_budget_changes(cfg, "u1", since=None)
    assert "projects" in result
    assert "expenses" in result
    assert "deleted_projects" in result
    assert "deleted_expenses" in result
    assert "now" in result


async def test_get_budget_changes_updated_at_in_response(cfg):
    """Projects returned by get_budget_changes include updated_at."""
    project = await _make_project(cfg)
    result = await budget_store.get_budget_changes(cfg, "u1", since=None)
    project_in_result = next(
        (p for p in result["projects"] if p["id"] == project["id"]), None
    )
    assert project_in_result is not None
    assert "updated_at" in project_in_result
    assert project_in_result["updated_at"] is not None
