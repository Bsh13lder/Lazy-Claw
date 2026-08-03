"""Expenses attached to a subtask (``project_expenses.subtask_id``).

Two user decisions this file exists to pin down (see
``docs/superpowers/plans/2026-08-03-sync-widget-parser-expenses.md`` Global
Constraints and ``docs/superpowers/specs/2026-08-03-diagnosis.md`` "Probe:
expenses"):

1. ROLLUP — a subtask expense still counts toward the parent task's total and
   the project budget. Achieved by the hard invariant ``subtask_id IS NOT
   NULL implies task_id IS NOT NULL``: every existing aggregation
   (``_spent_by_project`` GROUP BY project_id, the ``task_id = ?`` per-task
   filter) keeps working with ZERO changes.
2. DELETE POLICY — deleting/removing a subtask DEMOTES its expenses
   (``subtask_id = NULL``), never deletes them. Money is never lost. This is
   the opposite of the comment cascade in the same ``set_steps`` write, which
   DOES delete orphaned comments — comments are disposable invisible data,
   an expense is money.

Fixture modeled on ``tests/tasks/test_task_comments.py`` (store-level,
isolated temp DB) plus the route-level ``client`` fixture from
``tests/budgets/test_expense_is_favorite.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.budgets import store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.budgets import router as budgets_router
from lazyclaw.tasks import store as task_store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def client(cfg, monkeypatch):
    import lazyclaw.gateway.routes.budgets as routes_mod

    monkeypatch.setattr(routes_mod, "_config", cfg)
    app = FastAPI()
    app.include_router(budgets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    return TestClient(app)


@pytest.fixture
def far_future_cron(monkeypatch):
    """Pin ``get_next_run`` to a deterministic instant so the respawn test's
    next-occurrence timing is not flaky (mirrors
    ``tests/tasks/test_recurring_carry_forward.py``)."""
    fixed = datetime.now(timezone.utc) + timedelta(hours=30)

    def _fake_get_next_run(expr, user_id=None):  # noqa: ARG001
        return fixed

    monkeypatch.setattr(
        "lazyclaw.heartbeat.cron.get_next_run", _fake_get_next_run
    )
    return fixed


async def _project(cfg) -> str:
    proj = await store.create_project(cfg, "u1", "Kitchen Remodel")
    return proj["id"]


async def _task_with_steps(cfg, *titles: str) -> tuple[dict, list[dict]]:
    task = await task_store.create_task(
        cfg, "u1", "renovation", steps=[{"title": t} for t in titles],
    )
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    steps = task_store.decode_steps(fetched["steps"])
    return fetched, steps


# ---------------------------------------------------------------------------
# Create / read round-trip
# ---------------------------------------------------------------------------


async def test_create_and_list_round_trip_subtask_id(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]

    created = await store.create_expense(
        cfg, "u1", pid, amount=42.0, description="tiles",
        task_id=task["id"], subtask_id=step_id,
    )
    assert created["task_id"] == task["id"]
    assert created["subtask_id"] == step_id

    fetched = await store.list_expenses(cfg, "u1", project_id=pid)
    assert len(fetched) == 1
    assert fetched[0]["subtask_id"] == step_id
    assert fetched[0]["task_id"] == task["id"]


# ---------------------------------------------------------------------------
# Invariant: subtask_id IS NOT NULL implies task_id IS NOT NULL
# ---------------------------------------------------------------------------


async def test_bare_subtask_id_without_task_id_rejected(cfg) -> None:
    pid = await _project(cfg)
    with pytest.raises(ValueError):
        await store.create_expense(
            cfg, "u1", pid, amount=10.0, subtask_id="s-orphan",
        )


async def test_update_expense_rejects_clearing_task_id_while_subtask_id_stays(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]
    exp = await store.create_expense(
        cfg, "u1", pid, amount=10.0, task_id=task["id"], subtask_id=step_id,
    )

    with pytest.raises(ValueError):
        await store.update_expense(cfg, "u1", exp["id"], task_id=None)


# ---------------------------------------------------------------------------
# Unknown subtask_id for the given task is rejected
# ---------------------------------------------------------------------------


async def test_unknown_subtask_id_for_task_rejected(cfg) -> None:
    pid = await _project(cfg)
    task, _steps = await _task_with_steps(cfg, "buy tiles")

    with pytest.raises(ValueError):
        await store.create_expense(
            cfg, "u1", pid, amount=10.0,
            task_id=task["id"], subtask_id="s-does-not-exist",
        )


async def test_update_expense_rejects_unknown_subtask_id(cfg) -> None:
    pid = await _project(cfg)
    task, _steps = await _task_with_steps(cfg, "buy tiles")
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, task_id=task["id"])

    with pytest.raises(ValueError):
        await store.update_expense(
            cfg, "u1", exp["id"], subtask_id="s-does-not-exist",
        )


async def test_update_expense_accepts_valid_subtask_id(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]
    exp = await store.create_expense(cfg, "u1", pid, amount=10.0, task_id=task["id"])

    ok = await store.update_expense(cfg, "u1", exp["id"], subtask_id=step_id)
    assert ok is True

    fetched = await store.list_expenses(cfg, "u1", project_id=pid)
    assert fetched[0]["subtask_id"] == step_id


# ---------------------------------------------------------------------------
# "task not found" branch of _validate_subtask_link (review follow-up:
# every other invariant test above uses a REAL task with either a valid or
# unknown STEP id — none of them exercises task_id itself pointing at
# nothing / a soft-deleted row).
# ---------------------------------------------------------------------------


async def test_create_expense_rejects_nonexistent_task_id(cfg) -> None:
    pid = await _project(cfg)
    with pytest.raises(ValueError):
        await store.create_expense(
            cfg, "u1", pid, amount=10.0,
            task_id="does-not-exist", subtask_id="s-1",
        )


async def test_create_expense_rejects_soft_deleted_task_id(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]

    assert await task_store.delete_task(cfg, "u1", task["id"]) is True

    # get_task filters deleted_at IS NULL, so a soft-deleted task must
    # degrade the same way as a genuinely missing one — a clean ValueError,
    # never a 500 — even though the subtask_id it names was valid before the
    # delete.
    with pytest.raises(ValueError):
        await store.create_expense(
            cfg, "u1", pid, amount=10.0,
            task_id=task["id"], subtask_id=step_id,
        )


# ---------------------------------------------------------------------------
# list_expenses(subtask_id=...) exact-match filter
# ---------------------------------------------------------------------------


async def test_list_expenses_filters_by_subtask_id(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles", "hire plumber")
    step_a, step_b = steps[0]["id"], steps[1]["id"]

    exp_a = await store.create_expense(
        cfg, "u1", pid, amount=10.0, task_id=task["id"], subtask_id=step_a,
    )
    await store.create_expense(
        cfg, "u1", pid, amount=20.0, task_id=task["id"], subtask_id=step_b,
    )
    await store.create_expense(
        cfg, "u1", pid, amount=5.0, task_id=task["id"],
    )  # task-level, no subtask

    only_a = await store.list_expenses(cfg, "u1", project_id=pid, subtask_id=step_a)
    assert [e["id"] for e in only_a] == [exp_a["id"]]


# ---------------------------------------------------------------------------
# Rollup guarantee: project + per-task totals include a subtask expense
# ---------------------------------------------------------------------------


async def test_subtask_expense_rolls_up_into_project_and_task_totals(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]

    await store.create_expense(
        cfg, "u1", pid, amount=42.0, task_id=task["id"], subtask_id=step_id,
    )
    await store.create_expense(cfg, "u1", pid, amount=8.0)  # unrelated project-level

    # Project rollup (_spent_by_project via list_projects) — GROUP BY
    # project_id only, so the subtask expense is included automatically.
    projects = await store.list_projects(cfg, "u1")
    proj = next(p for p in projects if p["id"] == pid)
    assert proj["spent"] == pytest.approx(50.0)

    # Per-task rollup (the web TaskExpensePanel's client-side reduce over
    # list_expenses(task_id=...)) — the subtask expense still carries
    # task_id, so this exact-match filter still finds it.
    task_expenses = await store.list_expenses(cfg, "u1", task_id=task["id"])
    task_total = sum(e["amount"] for e in task_expenses)
    assert task_total == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# Demote (never delete) on set_steps dropping a step
# ---------------------------------------------------------------------------


async def test_set_steps_demotes_expense_on_dropped_subtask(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles", "hire plumber")
    step_a, step_b = steps[0], steps[1]

    exp = await store.create_expense(
        cfg, "u1", pid, amount=42.0, task_id=task["id"], subtask_id=step_a["id"],
    )

    # Drop step A from the checklist (simulates the user deleting it).
    await task_store.set_steps(cfg, "u1", task["id"], [step_b])

    fetched = await store.list_expenses(cfg, "u1", project_id=pid, status=None)
    demoted = next(e for e in fetched if e["id"] == exp["id"])
    assert demoted["subtask_id"] is None, "expense must survive, demoted to task-level"
    assert demoted["task_id"] == task["id"], "task_id link must be preserved"
    assert demoted["amount"] == pytest.approx(42.0), "money must not be lost"

    # Rollup still holds after the demote.
    projects = await store.list_projects(cfg, "u1")
    proj = next(p for p in projects if p["id"] == pid)
    assert proj["spent"] == pytest.approx(42.0)
    task_total = sum(
        e["amount"] for e in await store.list_expenses(cfg, "u1", task_id=task["id"])
    )
    assert task_total == pytest.approx(42.0)


async def test_set_steps_leaves_surviving_subtask_expense_untouched(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles", "hire plumber")
    step_a, step_b = steps[0], steps[1]

    exp_b = await store.create_expense(
        cfg, "u1", pid, amount=15.0, task_id=task["id"], subtask_id=step_b["id"],
    )

    # Drop step A only — step B survives, so its expense must stay linked.
    await task_store.set_steps(cfg, "u1", task["id"], [step_b])

    fetched = await store.list_expenses(cfg, "u1", project_id=pid)
    survivor = next(e for e in fetched if e["id"] == exp_b["id"])
    assert survivor["subtask_id"] == step_b["id"]


# ---------------------------------------------------------------------------
# Demote (never delete) on delete_task
# ---------------------------------------------------------------------------


async def test_delete_task_demotes_subtask_expense_but_keeps_task_id(cfg) -> None:
    pid = await _project(cfg)
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]

    exp = await store.create_expense(
        cfg, "u1", pid, amount=42.0, task_id=task["id"], subtask_id=step_id,
    )

    assert await task_store.delete_task(cfg, "u1", task["id"]) is True

    fetched = await store.list_expenses(cfg, "u1", project_id=pid, status=None)
    demoted = next(e for e in fetched if e["id"] == exp["id"])
    assert demoted["subtask_id"] is None, "subtask link must be cleared on task delete"
    # NOTE: task_id is intentionally left dangling here — this is a
    # pre-existing hole (delete_task never touched project_expenses.task_id
    # before this change either; see docs/superpowers/specs/
    # 2026-08-03-diagnosis.md "Probe: expenses" / the plan's Task 7 brief).
    # Widening the fix to null task_id too is explicitly out of scope for
    # this task — only the NEW subtask_id link is guaranteed demoted.
    assert demoted["task_id"] == task["id"]
    assert demoted["amount"] == pytest.approx(42.0), "money must not be lost"


# ---------------------------------------------------------------------------
# Respawn: a subtask expense stays pinned to the completed occurrence
# ---------------------------------------------------------------------------


async def test_respawn_leaves_subtask_expense_pinned_to_completed_occurrence(
    cfg, far_future_cron
) -> None:
    """``project_expenses.subtask_id`` is NOT a ``tasks`` column, so it is
    outside the ``_RESPAWN_CARRY_COLUMNS`` / ``_RESPAWN_DERIVED_COLUMNS`` /
    ``_RESPAWN_RESET_COLUMNS`` disposition guard in
    ``tests/tasks/test_recurring_carry_forward.py`` — that guard only
    classifies columns on the ``tasks`` table. The respawn re-mints every
    step id (``tasks/store.py`` ~:1596-1602) AND creates a brand-new task row
    id (``_RESPAWN_RESET_COLUMNS`` includes ``"id"``), so an expense keyed on
    ``(task_id=old, subtask_id=old-step)`` simply cannot resolve against the
    new occurrence — it stays attached to the completed one, which is the
    correct ledger semantics (the money was spent on THAT occurrence).
    """
    pid = await _project(cfg)
    task = await task_store.create_task(
        cfg, "u1", "weekly grocery run",
        recurring="0 9 * * 1",
        steps=[{"title": "milk"}],
    )
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    step_id = task_store.decode_steps(fetched["steps"])[0]["id"]

    exp = await store.create_expense(
        cfg, "u1", pid, amount=12.5, task_id=task["id"], subtask_id=step_id,
    )

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    open_tasks = [t for t in await task_store.list_tasks(cfg, "u1") if t["status"] != "done"]
    assert len(open_tasks) == 1, "exactly one next occurrence should be spawned"
    new_task = open_tasks[0]
    assert new_task["id"] != task["id"], "respawn must mint a new task row id"

    # The expense is untouched — still pinned to the OLD (now-completed) task.
    fetched_exp = await store.list_expenses(cfg, "u1", project_id=pid, status=None)
    persisted = next(e for e in fetched_exp if e["id"] == exp["id"])
    assert persisted["task_id"] == task["id"]
    assert persisted["subtask_id"] == step_id

    # It must NOT appear in the new occurrence's totals.
    new_task_expenses = await store.list_expenses(cfg, "u1", task_id=new_task["id"])
    assert new_task_expenses == []

    # But it still rolls up into the project total (money is never lost).
    projects = await store.list_projects(cfg, "u1")
    proj = next(p for p in projects if p["id"] == pid)
    assert proj["spent"] == pytest.approx(12.5)


# ---------------------------------------------------------------------------
# Route-level: create body, patch body, list filter, ValueError -> 400
# ---------------------------------------------------------------------------


async def test_route_create_expense_with_subtask_id(client, cfg) -> None:
    pid = client.post(
        "/api/budgets/projects", json={"name": "Kitchen"}
    ).json()["project"]["id"]
    task, steps = await _task_with_steps(cfg, "buy tiles")
    step_id = steps[0]["id"]

    r = client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 42.0, "task_id": task["id"], "subtask_id": step_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["expense"]["subtask_id"] == step_id


async def test_route_create_expense_bare_subtask_id_is_400(client) -> None:
    pid = client.post(
        "/api/budgets/projects", json={"name": "Kitchen"}
    ).json()["project"]["id"]

    r = client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 42.0, "subtask_id": "s-orphan"},
    )
    assert r.status_code == 400, r.text


async def test_route_create_expense_missing_project_is_still_404(client) -> None:
    r = client.post(
        "/api/budgets/projects/does-not-exist/expenses",
        json={"amount": 42.0},
    )
    assert r.status_code == 404, r.text


async def test_route_patch_expense_subtask_id_bad_invariant_is_400(client, cfg) -> None:
    pid = client.post(
        "/api/budgets/projects", json={"name": "Kitchen"}
    ).json()["project"]["id"]
    eid = client.post(
        f"/api/budgets/projects/{pid}/expenses", json={"amount": 5.0}
    ).json()["expense"]["id"]

    r = client.patch(
        f"/api/budgets/expenses/{eid}", json={"subtask_id": "s-orphan"}
    )
    assert r.status_code == 400, r.text


async def test_route_list_expenses_subtask_filter(client, cfg) -> None:
    pid = client.post(
        "/api/budgets/projects", json={"name": "Kitchen"}
    ).json()["project"]["id"]
    task, steps = await _task_with_steps(cfg, "buy tiles", "hire plumber")
    step_a, step_b = steps[0]["id"], steps[1]["id"]

    eid_a = client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 10.0, "task_id": task["id"], "subtask_id": step_a},
    ).json()["expense"]["id"]
    client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 20.0, "task_id": task["id"], "subtask_id": step_b},
    )

    r = client.get(f"/api/budgets/projects/{pid}/expenses?subtask_id={step_a}")
    assert r.status_code == 200, r.text
    listed = r.json()["expenses"]
    assert [e["id"] for e in listed] == [eid_a]
