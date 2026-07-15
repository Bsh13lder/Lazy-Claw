"""PATCH budgets routes must let a client CLEAR a nullable field with explicit null.

Regression (P2): ``update_expense_route`` / ``update_project_route`` used
``{k: v for ... if v is not None}``, which conflated "field absent" (leave alone)
with "field is null" (clear). The web sends ``{"vendor": null}`` to unset the
vendor — that got filtered out, so the field silently reappeared (the same gap
the tasks PATCH route already fixed via ``exclude_unset``).

Fix is route-level: ``model_dump(exclude_unset=True)`` keeps explicit nulls while
a truly-omitted field is left untouched, with a guard that drops a stray null on
the NOT-NULL structural columns (expense amount/currency/status; project
name/budget/currency/status) so a bad payload can never blank them → 500.

Mobile only ever sends the fields it changed (never explicit null), so it is
unaffected — pinned by the ``*_omitted_field_left_untouched`` tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.budgets import router as budgets_router

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


def _project(client, **fields) -> dict:
    r = client.post("/api/budgets/projects", json={"name": "Alpha", **fields})
    assert r.status_code == 200, r.text
    return r.json()["project"]


def _expense(client, pid, **fields) -> dict:
    r = client.post(
        f"/api/budgets/projects/{pid}/expenses",
        json={"amount": 10.0, **fields},
    )
    assert r.status_code == 200, r.text
    return r.json()["expense"]


def _read_expense(client, pid, eid) -> dict:
    listed = client.get(f"/api/budgets/projects/{pid}/expenses").json()["expenses"]
    return next(e for e in listed if e["id"] == eid)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


async def test_clear_expense_vendor_with_explicit_null(client):
    pid = _project(client)["id"]
    exp = _expense(client, pid, vendor="Acme", description="lunch")
    assert exp["vendor"] == "Acme"

    r = client.patch(f"/api/budgets/expenses/{exp['id']}", json={"vendor": None})
    assert r.status_code == 200, r.text

    fetched = _read_expense(client, pid, exp["id"])
    assert fetched["vendor"] in (None, "")
    assert fetched["description"] == "lunch"  # untouched


async def test_clear_expense_notes_with_explicit_null(client):
    pid = _project(client)["id"]
    exp = _expense(client, pid, notes="pay later")
    assert exp["notes"] == "pay later"

    r = client.patch(f"/api/budgets/expenses/{exp['id']}", json={"notes": None})
    assert r.status_code == 200, r.text
    assert _read_expense(client, pid, exp["id"])["notes"] in (None, "")


async def test_expense_omitted_field_left_untouched(client):
    """Patching only description must NOT clear the vendor (absent != clear)."""
    pid = _project(client)["id"]
    exp = _expense(client, pid, vendor="Acme", description="old")

    r = client.patch(
        f"/api/budgets/expenses/{exp['id']}", json={"description": "new"}
    )
    assert r.status_code == 200, r.text
    fetched = _read_expense(client, pid, exp["id"])
    assert fetched["description"] == "new"
    assert fetched["vendor"] == "Acme"  # untouched, not cleared


async def test_null_amount_ignored_but_other_fields_apply(client):
    """A stray null amount must never blank the NOT-NULL amount column, but
    other fields in the same patch still apply."""
    pid = _project(client)["id"]
    exp = _expense(client, pid, vendor="Acme")
    assert exp["amount"] == 10.0

    r = client.patch(
        f"/api/budgets/expenses/{exp['id']}",
        json={"amount": None, "vendor": "NewVendor"},
    )
    assert r.status_code == 200, r.text
    fetched = _read_expense(client, pid, exp["id"])
    assert fetched["amount"] == 10.0  # unchanged
    assert fetched["vendor"] == "NewVendor"


async def test_expense_empty_patch_still_400(client):
    pid = _project(client)["id"]
    exp = _expense(client, pid)
    r = client.patch(f"/api/budgets/expenses/{exp['id']}", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def test_clear_project_description_with_explicit_null(client):
    proj = _project(client, description="a big project")
    assert proj["description"] == "a big project"

    r = client.patch(
        f"/api/budgets/projects/{proj['id']}", json={"description": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["project"]["description"] in (None, "")


async def test_clear_project_color_with_explicit_null(client):
    proj = _project(client, color="#4F8AF4")
    assert proj["color"] == "#4F8AF4"

    r = client.patch(f"/api/budgets/projects/{proj['id']}", json={"color": None})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["color"] in (None, "")


async def test_null_name_ignored_but_other_fields_apply(client):
    """A stray null name must never blank the always-required project name."""
    proj = _project(client, description="keep")

    r = client.patch(
        f"/api/budgets/projects/{proj['id']}",
        json={"name": None, "description": "changed"},
    )
    assert r.status_code == 200, r.text
    out = r.json()["project"]
    assert out["name"] == "Alpha"  # unchanged
    assert out["description"] == "changed"


async def test_project_omitted_field_left_untouched(client):
    proj = _project(client, description="old", color="#4F8AF4")

    r = client.patch(
        f"/api/budgets/projects/{proj['id']}", json={"description": "new"}
    )
    assert r.status_code == 200, r.text
    out = r.json()["project"]
    assert out["description"] == "new"
    assert out["color"] == "#4F8AF4"  # untouched


async def test_project_empty_patch_still_400(client):
    proj = _project(client)
    r = client.patch(f"/api/budgets/projects/{proj['id']}", json={})
    assert r.status_code == 400
