"""Budgets API — project budgets + expenses + recurring rules.

Exposes ``lazyclaw.budgets.store`` to the web UI. The store is scoped by
``user_id`` and free-text fields are AES-256-GCM encrypted at rest; amounts
are plaintext so totals SUM in SQL. Every expense mirrors to a LazyBrain note
that wikilinks back to its project page.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lazyclaw.budgets import store
from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateProjectBody(BaseModel):
    # Optional client-minted id for offline-first idempotent replay.
    # When provided, the server uses it as the project id. A second POST
    # with the same id returns the existing project without duplicating it.
    id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    budget: float = 0.0
    currency: str = Field(default="EUR", max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    # Optional calendar color, e.g. "#4F8AF4". Validated lenient server-side:
    # a non-#RRGGBB value is cleared to None (never 500s).
    color: str | None = Field(default=None, max_length=16)
    # Optional favorite flag — pins the project into the mobile Home
    # "Favorites" section. None = leave as-is on an upsert.
    is_favorite: bool | None = None
    # Optional time frame — plaintext YYYY-MM-DD. Lenient server-side: an
    # unparseable date is cleared to None (never 500s), a datetime truncates
    # to its date part.
    start_date: str | None = Field(default=None, max_length=32)
    due_date: str | None = Field(default=None, max_length=32)


class SetBudgetBody(BaseModel):
    budget: float
    currency: str | None = Field(default=None, max_length=8)


class UpdateProjectBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    budget: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None
    # Optional calendar color, e.g. "#4F8AF4". Validated lenient server-side:
    # a non-#RRGGBB value is cleared to None (never 500s).
    color: str | None = Field(default=None, max_length=16)
    # Optional favorite flag — toggled from the mobile star control. None means
    # "leave unchanged" (dropped by the route's None-filter); True/False set it.
    is_favorite: bool | None = None
    # Optional time frame — YYYY-MM-DD; empty string clears (normalized to
    # NULL by the store's lenient _clean_project_date).
    start_date: str | None = Field(default=None, max_length=32)
    due_date: str | None = Field(default=None, max_length=32)


class CreateExpenseBody(BaseModel):
    # Optional client-minted id for offline-first idempotent replay.
    id: str | None = Field(default=None, max_length=128)
    amount: float
    currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    vendor: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    task_id: str | None = None
    spent_at: str | None = None


class UpdateExpenseBody(BaseModel):
    amount: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    vendor: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    project_id: str | None = None
    task_id: str | None = None
    spent_at: str | None = None
    status: Literal["posted", "void"] | None = None
    # Per-expense favorite flag (star). None = leave unchanged (dropped by the
    # route's None-filter); True/False set it — powers the "starred only" overview.
    is_favorite: bool | None = None


class CreateRecurringBody(BaseModel):
    amount: float
    cron_expression: str = Field(min_length=1, max_length=100)
    currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    vendor: str | None = Field(default=None, max_length=200)
    task_id: str | None = None


class AddBudgetEntryBody(BaseModel):
    # Optional client-minted id for offline-first idempotent replay. A retried
    # POST with the same id returns the existing entry without double-adding to
    # the project budget.
    id: str | None = Field(default=None, max_length=128)
    amount: float
    source: str | None = Field(default=None, max_length=500)
    currency: str | None = Field(default=None, max_length=8)


class UpdateBudgetEntryBody(BaseModel):
    amount: float | None = None
    source: str | None = Field(default=None, max_length=500)
    currency: str | None = Field(default=None, max_length=8)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects_route(
    status: Literal["active", "archived", "all"] = Query("all"),
    user: User = Depends(get_current_user),
):
    status_filter = None if status == "all" else status
    projects = await store.list_projects(_config, user.id, status=status_filter)
    return {"projects": projects, "count": len(projects)}


@router.post("/projects")
async def create_project_route(
    body: CreateProjectBody,
    user: User = Depends(get_current_user),
):
    logger.debug(
        "[route:budgets] POST create-project user=%s fields=%s client_id=%s",
        user.id, list(body.model_dump(exclude_unset=True).keys()), bool(body.id),
    )
    project = await store.create_project(
        _config, user.id, body.name,
        budget=body.budget, currency=body.currency, description=body.description,
        color=body.color, is_favorite=body.is_favorite,
        start_date=body.start_date, due_date=body.due_date,
        project_id=body.id or None,
    )
    return {"project": project}


@router.get("/projects/{project_id}")
async def get_project_route(
    project_id: str,
    user: User = Depends(get_current_user),
):
    project = await store.get_project(_config, user.id, project_id)
    if project is None:
        logger.warning(
            "[route:budgets] GET project id=%s user=%s -> 404 project not found",
            project_id, user.id,
        )
        raise HTTPException(status_code=404, detail="project not found")
    return {"project": project}


@router.patch("/projects/{project_id}")
async def update_project_route(
    project_id: str,
    body: UpdateProjectBody,
    user: User = Depends(get_current_user),
):
    # ``exclude_unset`` keeps an explicit ``null`` (clear the field) but drops a
    # truly-omitted field (leave it untouched). A stray null on a NOT-NULL
    # column (name/budget/currency/status) is guarded out so it can never blank
    # a required field → 500. Nullable fields (description, color) clear fine.
    fields = body.model_dump(exclude_unset=True)
    logger.debug(
        "[route:budgets] PATCH project id=%s user=%s fields=%s",
        project_id, user.id, list(fields.keys()),
    )
    for required in ("name", "budget", "currency", "status"):
        if required in fields and fields[required] is None:
            fields.pop(required)
    if not fields:
        logger.warning(
            "[route:budgets] PATCH project id=%s user=%s -> 400 no fields to update",
            project_id, user.id,
        )
        raise HTTPException(status_code=400, detail="no fields to update")
    ok = await store.update_project(_config, user.id, project_id, **fields)
    if not ok:
        logger.warning(
            "[route:budgets] PATCH project id=%s user=%s -> 404 project not found",
            project_id, user.id,
        )
        raise HTTPException(status_code=404, detail="project not found")
    project = await store.get_project(_config, user.id, project_id)
    return {"project": project}


@router.put("/projects/{project_id}/budget")
async def set_budget_route(
    project_id: str,
    body: SetBudgetBody,
    user: User = Depends(get_current_user),
):
    ok = await store.set_budget(
        _config, user.id, project_id, body.budget, currency=body.currency,
    )
    if not ok:
        logger.warning(
            "[route:budgets] PUT set-budget project=%s user=%s -> 404 project not found",
            project_id, user.id,
        )
        raise HTTPException(status_code=404, detail="project not found")
    project = await store.get_project(_config, user.id, project_id)
    return {"project": project}


@router.delete("/projects/{project_id}")
async def delete_project_route(
    project_id: str,
    cascade: bool = Query(False),
    user: User = Depends(get_current_user),
):
    ok = await store.delete_project(_config, user.id, project_id, cascade=cascade)
    if not ok:
        logger.warning(
            "[route:budgets] DELETE project id=%s user=%s cascade=%s -> 409 project has expenses",
            project_id, user.id, cascade,
        )
        raise HTTPException(
            status_code=409,
            detail="project has expenses; pass ?cascade=true to delete anyway",
        )
    return {"status": "deleted"}


@router.get("/changes")
async def budget_changes_route(
    user: User = Depends(get_current_user),
    since: str | None = Query(
        default=None,
        description=(
            "ISO-8601 datetime. Only projects/expenses updated after this "
            "timestamp are returned. Omit to receive all rows (full sync). "
            "Use the `now` field from the previous response as the next "
            "`since` value."
        ),
    ),
):
    """Delta feed for offline-first clients.

    Returns:
    - ``projects``: live (non-deleted) projects updated after ``since``
    - ``expenses``: live (non-deleted) expenses updated after ``since``
    - ``deleted_projects``: ids of projects soft-deleted after ``since``
    - ``deleted_expenses``: ids of expenses soft-deleted after ``since``
    - ``now``: server ISO timestamp — pass this as ``since`` next time

    Clients should persist ``now`` locally and send it on the next pull.
    Last-write-wins on ``updated_at`` resolves any conflicts.
    """
    result = await store.get_budget_changes(_config, user.id, since=since)
    logger.debug(
        "[route:budgets] GET changes user=%s since=%s -> projects=%d expenses=%d "
        "entries=%d del_projects=%d del_expenses=%d del_entries=%d now=%s",
        user.id, since,
        len(result.get("projects") or []),
        len(result.get("expenses") or []),
        len(result.get("budget_entries") or []),
        len(result.get("deleted_projects") or []),
        len(result.get("deleted_expenses") or []),
        len(result.get("deleted_budget_entries") or []),
        result.get("now"),
    )
    return result


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


@router.get("/expenses")
async def list_all_expenses_route(
    user: User = Depends(get_current_user),
):
    """Cross-project expense ledger for the global Expenses view — every
    posted expense, newest-first, each carrying its ``project_name``."""
    expenses = await store.list_all_expenses(_config, user.id)
    return {"expenses": expenses, "count": len(expenses)}


@router.get("/projects/{project_id}/expenses")
async def list_expenses_route(
    project_id: str,
    task_id: str | None = Query(None),
    user: User = Depends(get_current_user),
):
    expenses = await store.list_expenses(
        _config, user.id, project_id=project_id, task_id=task_id,
    )
    return {"expenses": expenses, "count": len(expenses)}


@router.post("/projects/{project_id}/expenses")
async def create_expense_route(
    project_id: str,
    body: CreateExpenseBody,
    user: User = Depends(get_current_user),
):
    logger.debug(
        "[route:budgets] POST create-expense project=%s user=%s fields=%s client_id=%s",
        project_id, user.id, list(body.model_dump(exclude_unset=True).keys()),
        bool(body.id),
    )
    try:
        expense = await store.create_expense(
            _config, user.id, project_id,
            amount=body.amount, currency=body.currency,
            description=body.description, vendor=body.vendor, notes=body.notes,
            task_id=body.task_id, spent_at=body.spent_at,
            expense_id=body.id or None,
        )
    except ValueError as exc:
        logger.warning(
            "[route:budgets] POST create-expense project=%s user=%s -> 404: %s",
            project_id, user.id, exc,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"expense": expense}


@router.patch("/expenses/{expense_id}")
async def update_expense_route(
    expense_id: str,
    body: UpdateExpenseBody,
    user: User = Depends(get_current_user),
):
    # ``exclude_unset`` keeps an explicit ``null`` (clear the field) but drops a
    # truly-omitted field (leave it untouched). A stray null on a NOT-NULL
    # column (amount/currency/status) is guarded out so it can never blank a
    # required field or corrupt the SUM rollup. Nullable fields (vendor, notes,
    # description, task_id, spent_at) clear fine.
    fields = body.model_dump(exclude_unset=True)
    logger.debug(
        "[route:budgets] PATCH expense id=%s user=%s fields=%s",
        expense_id, user.id, list(fields.keys()),
    )
    for required in ("amount", "currency", "status"):
        if required in fields and fields[required] is None:
            fields.pop(required)
    if "project_id" in fields:
        if not fields["project_id"]:
            raise HTTPException(400, "project_id cannot be null — every expense belongs to a project")
        target = await store.get_project(_config, user.id, fields["project_id"])
        if target is None:
            raise HTTPException(404, "project not found")
    if not fields:
        logger.warning(
            "[route:budgets] PATCH expense id=%s user=%s -> 400 no fields to update",
            expense_id, user.id,
        )
        raise HTTPException(status_code=400, detail="no fields to update")
    ok = await store.update_expense(_config, user.id, expense_id, **fields)
    if not ok:
        logger.warning(
            "[route:budgets] PATCH expense id=%s user=%s -> 404 expense not found",
            expense_id, user.id,
        )
        raise HTTPException(status_code=404, detail="expense not found")
    return {"status": "updated"}


@router.delete("/expenses/{expense_id}")
async def delete_expense_route(
    expense_id: str,
    user: User = Depends(get_current_user),
):
    ok = await store.delete_expense(_config, user.id, expense_id)
    if not ok:
        logger.warning(
            "[route:budgets] DELETE expense id=%s user=%s -> 404 expense not found",
            expense_id, user.id,
        )
        raise HTTPException(status_code=404, detail="expense not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Recurring + report
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/recurring")
async def create_recurring_route(
    project_id: str,
    body: CreateRecurringBody,
    user: User = Depends(get_current_user),
):
    logger.debug(
        "[route:budgets] POST create-recurring project=%s user=%s fields=%s",
        project_id, user.id, list(body.model_dump(exclude_unset=True).keys()),
    )
    try:
        recurring = await store.create_recurring_expense(
            _config, user.id, project_id,
            amount=body.amount, cron_expression=body.cron_expression,
            currency=body.currency, description=body.description,
            vendor=body.vendor, task_id=body.task_id,
        )
    except ValueError as exc:
        # Do not interpolate exc — its message can echo the submitted cron
        # expression value. Log only the route + user + a static reason.
        logger.warning(
            "[route:budgets] POST create-recurring project=%s user=%s -> 400 (cron/project validation)",
            project_id, user.id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"recurring": recurring}


@router.get("/projects/{project_id}/recurring")
async def list_recurring_route(
    project_id: str,
    user: User = Depends(get_current_user),
):
    rules = await store.list_recurring(_config, user.id, project_id=project_id)
    return {"recurring": rules, "count": len(rules)}


@router.post("/projects/{project_id}/budget-entries")
async def add_budget_entry_route(
    project_id: str,
    body: AddBudgetEntryBody,
    user: User = Depends(get_current_user),
):
    logger.debug(
        "[route:budgets] POST add-budget-entry project=%s user=%s fields=%s client_id=%s",
        project_id, user.id, list(body.model_dump(exclude_unset=True).keys()),
        bool(body.id),
    )
    try:
        entry = await store.add_budget_entry(
            _config, user.id, project_id,
            amount=body.amount, source=body.source, currency=body.currency,
            entry_id=body.id or None,
        )
    except ValueError as exc:
        logger.warning(
            "[route:budgets] POST add-budget-entry project=%s user=%s -> 404: %s",
            project_id, user.id, exc,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"entry": entry}


@router.get("/projects/{project_id}/budget-entries")
async def list_budget_entries_route(
    project_id: str,
    user: User = Depends(get_current_user),
):
    entries = await store.list_budget_entries(_config, user.id, project_id)
    return {"entries": entries, "count": len(entries)}


@router.patch("/entries/{entry_id}")
async def update_budget_entry_route(
    entry_id: str,
    body: UpdateBudgetEntryBody,
    user: User = Depends(get_current_user),
):
    fields: dict = {}
    if body.amount is not None:
        fields["amount"] = body.amount
    if body.source is not None:
        fields["source"] = body.source
    if body.currency is not None:
        fields["currency"] = body.currency
    logger.debug(
        "[route:budgets] PATCH budget-entry id=%s user=%s fields=%s",
        entry_id, user.id, list(fields.keys()),
    )
    if not fields:
        logger.warning(
            "[route:budgets] PATCH budget-entry id=%s user=%s -> 400 no fields to update",
            entry_id, user.id,
        )
        raise HTTPException(status_code=400, detail="no fields to update")
    ok = await store.update_budget_entry(_config, user.id, entry_id, **fields)
    if not ok:
        logger.warning(
            "[route:budgets] PATCH budget-entry id=%s user=%s -> 404 budget entry not found",
            entry_id, user.id,
        )
        raise HTTPException(status_code=404, detail="budget entry not found")
    entry = await store.get_budget_entry(_config, user.id, entry_id)
    return {"entry": entry}


@router.delete("/entries/{entry_id}")
async def delete_budget_entry_route(
    entry_id: str,
    user: User = Depends(get_current_user),
):
    ok = await store.delete_budget_entry(_config, user.id, entry_id)
    if not ok:
        logger.warning(
            "[route:budgets] DELETE budget-entry id=%s user=%s -> 404 budget entry not found",
            entry_id, user.id,
        )
        raise HTTPException(status_code=404, detail="budget entry not found")
    return {"status": "deleted"}


@router.get("/report")
async def report_route(
    project_id: str | None = Query(None),
    user: User = Depends(get_current_user),
):
    return await store.spending_report(_config, user.id, project_id=project_id)
