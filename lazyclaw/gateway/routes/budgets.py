"""Budgets API — project budgets + expenses + recurring rules.

Exposes ``lazyclaw.budgets.store`` to the web UI. The store is scoped by
``user_id`` and free-text fields are AES-256-GCM encrypted at rest; amounts
are plaintext so totals SUM in SQL. Every expense mirrors to a LazyBrain note
that wikilinks back to its project page.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lazyclaw.budgets import store
from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

_config = load_config()

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    budget: float = 0.0
    currency: str = Field(default="EUR", max_length=8)
    description: str | None = Field(default=None, max_length=2000)


class SetBudgetBody(BaseModel):
    budget: float
    currency: str | None = Field(default=None, max_length=8)


class UpdateProjectBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    budget: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class CreateExpenseBody(BaseModel):
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
    task_id: str | None = None
    spent_at: str | None = None
    status: Literal["posted", "void"] | None = None


class CreateRecurringBody(BaseModel):
    amount: float
    cron_expression: str = Field(min_length=1, max_length=100)
    currency: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=2000)
    vendor: str | None = Field(default=None, max_length=200)
    task_id: str | None = None


class AddBudgetEntryBody(BaseModel):
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
    project = await store.create_project(
        _config, user.id, body.name,
        budget=body.budget, currency=body.currency, description=body.description,
    )
    return {"project": project}


@router.get("/projects/{project_id}")
async def get_project_route(
    project_id: str,
    user: User = Depends(get_current_user),
):
    project = await store.get_project(_config, user.id, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return {"project": project}


@router.patch("/projects/{project_id}")
async def update_project_route(
    project_id: str,
    body: UpdateProjectBody,
    user: User = Depends(get_current_user),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    ok = await store.update_project(_config, user.id, project_id, **fields)
    if not ok:
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
        raise HTTPException(
            status_code=409,
            detail="project has expenses; pass ?cascade=true to delete anyway",
        )
    return {"status": "deleted"}


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
    try:
        expense = await store.create_expense(
            _config, user.id, project_id,
            amount=body.amount, currency=body.currency,
            description=body.description, vendor=body.vendor, notes=body.notes,
            task_id=body.task_id, spent_at=body.spent_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"expense": expense}


@router.patch("/expenses/{expense_id}")
async def update_expense_route(
    expense_id: str,
    body: UpdateExpenseBody,
    user: User = Depends(get_current_user),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    ok = await store.update_expense(_config, user.id, expense_id, **fields)
    if not ok:
        raise HTTPException(status_code=404, detail="expense not found")
    return {"status": "updated"}


@router.delete("/expenses/{expense_id}")
async def delete_expense_route(
    expense_id: str,
    user: User = Depends(get_current_user),
):
    ok = await store.delete_expense(_config, user.id, expense_id)
    if not ok:
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
    try:
        recurring = await store.create_recurring_expense(
            _config, user.id, project_id,
            amount=body.amount, cron_expression=body.cron_expression,
            currency=body.currency, description=body.description,
            vendor=body.vendor, task_id=body.task_id,
        )
    except ValueError as exc:
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
    try:
        entry = await store.add_budget_entry(
            _config, user.id, project_id,
            amount=body.amount, source=body.source, currency=body.currency,
        )
    except ValueError as exc:
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
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    ok = await store.update_budget_entry(_config, user.id, entry_id, **fields)
    if not ok:
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
        raise HTTPException(status_code=404, detail="budget entry not found")
    return {"status": "deleted"}


@router.get("/report")
async def report_route(
    project_id: str | None = Query(None),
    user: User = Depends(get_current_user),
):
    return await store.spending_report(_config, user.id, project_id=project_id)
