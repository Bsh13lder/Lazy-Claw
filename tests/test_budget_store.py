"""Tests for the project budget + expense store (lazyclaw/budgets/store.py).

Covers: idempotent project upsert by name_key, encrypted round-trip, plaintext
SUM rollup with void exclusion, per-expense LazyBrain wikilink edge, currency
default/override, mixed-currency flag, and delete 409-vs-cascade.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

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
    try:
        yield c
    finally:
        await close_pool()


async def test_create_project_idempotent_by_name_key(cfg):
    a = await store.create_project(cfg, "u1", "Nima", budget=500)
    b = await store.create_project(cfg, "u1", "  nima ")  # case + whitespace
    assert a["id"] == b["id"], "same name_key must upsert, not duplicate"

    projects = await store.list_projects(cfg, "u1")
    assert len(projects) == 1
    assert projects[0]["name_key"] == "nima"


async def test_project_encrypted_at_rest(cfg):
    await store.create_project(cfg, "u1", "Secret Co", description="confidential")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT name, name_key, description FROM projects WHERE user_id = 'u1'"
        )
        row = await cur.fetchone()
    enc_name, name_key, enc_desc = row
    assert enc_name.startswith("enc:"), "name must be encrypted at rest"
    assert enc_desc.startswith("enc:"), "description must be encrypted at rest"
    assert name_key == "secret co", "name_key stays plaintext for joins"

    proj = await store.get_project_by_name(cfg, "u1", "secret co")
    assert proj["name"] == "Secret Co"
    assert proj["description"] == "confidential"


async def test_list_projects_rollup_and_void_exclusion(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    await store.create_expense(cfg, "u1", proj["id"], amount=40)
    e2 = await store.create_expense(cfg, "u1", proj["id"], amount=60)
    await store.update_expense(cfg, "u1", e2["id"], status="void")

    projects = await store.list_projects(cfg, "u1")
    p = projects[0]
    assert p["spent"] == 40, "void expenses excluded from spent"
    assert p["remaining"] == 460


async def test_create_expense_writes_project_wikilink_edge(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    exp = await store.create_expense(
        cfg, "u1", proj["id"], amount=40, description="hosting",
    )
    assert exp["lazybrain_note_id"], "expense must mirror a LazyBrain note"

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT to_page_name FROM note_links "
            "WHERE user_id = 'u1' AND from_note_id = ?",
            (exp["lazybrain_note_id"],),
        )
        edges = [r[0] for r in await cur.fetchall()]
    assert "nima project" in edges, "expense note must wikilink the project page"


async def test_currency_default_and_override(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500, currency="EUR")
    default = await store.create_expense(cfg, "u1", proj["id"], amount=10)
    override = await store.create_expense(
        cfg, "u1", proj["id"], amount=10, currency="USD",
    )
    assert default["currency"] == "EUR", "inherits project currency"
    assert override["currency"] == "USD", "override respected"


async def test_mixed_currency_flagged_in_report(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500, currency="EUR")
    await store.create_expense(cfg, "u1", proj["id"], amount=10, currency="USD")
    report = await store.spending_report(cfg, "u1", project_id=proj["id"])
    assert report["projects"][0]["mixed_currency"] is True


async def test_list_all_expenses_cross_project(cfg):
    a = await store.create_project(cfg, "u1", "Alpha", budget=100)
    b = await store.create_project(cfg, "u1", "Beta", budget=100)
    await store.create_expense(cfg, "u1", a["id"], amount=10, description="x")
    await store.create_expense(cfg, "u1", b["id"], amount=20, description="y")

    rows = await store.list_all_expenses(cfg, "u1")
    assert len(rows) == 2
    by_name = {r["project_name"]: r["amount"] for r in rows}
    assert by_name == {"Alpha": 10.0, "Beta": 20.0}
    # Each row carries its project id for grouping in the UI.
    assert all(r.get("project_id") for r in rows)


async def test_list_all_expenses_excludes_void(cfg):
    a = await store.create_project(cfg, "u1", "Alpha", budget=100)
    keep = await store.create_expense(cfg, "u1", a["id"], amount=10)
    drop = await store.create_expense(cfg, "u1", a["id"], amount=99)
    await store.update_expense(cfg, "u1", drop["id"], status="void")

    rows = await store.list_all_expenses(cfg, "u1")
    assert [r["id"] for r in rows] == [keep["id"]]


async def test_delete_project_refuses_then_cascade(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    await store.create_expense(cfg, "u1", proj["id"], amount=40)

    refused = await store.delete_project(cfg, "u1", proj["id"])
    assert refused is False, "must refuse delete when expenses exist"

    ok = await store.delete_project(cfg, "u1", proj["id"], cascade=True)
    assert ok is True
    assert await store.get_project(cfg, "u1", proj["id"]) is None
    assert await store.list_expenses(cfg, "u1", project_id=proj["id"], status=None) == []


async def test_add_budget_entry_bumps_budget_and_records_source(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    entry = await store.add_budget_entry(
        cfg, "u1", proj["id"], amount=200, source="client deposit",
    )
    assert entry["amount"] == 200
    assert entry["new_budget"] == 700
    assert entry["source"] == "client deposit"

    # Budget on the project reflects the top-up.
    refreshed = await store.get_project(cfg, "u1", proj["id"])
    assert refreshed["budget"] == 700

    # Source is encrypted at rest, plaintext on read.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT source FROM budget_entries WHERE id = ?", (entry["id"],)
        )
        raw = (await cur.fetchone())[0]
    assert raw.startswith("enc:")

    entries = await store.list_budget_entries(cfg, "u1", proj["id"])
    assert len(entries) == 1
    assert entries[0]["source"] == "client deposit"


async def test_set_budget_writes_audit_entry(cfg):
    """Edit budget MUST leave a tracked ledger entry (the bug the user hit:
    'changes don't track'). The audit row captures the delta + before→after."""
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    await store.set_budget(cfg, "u1", proj["id"], 700)
    entries = await store.list_budget_entries(cfg, "u1", proj["id"])
    edit_rows = [e for e in entries if e["kind"] == "edit"]
    assert len(edit_rows) == 1
    assert edit_rows[0]["amount"] == 200
    assert "500" in (edit_rows[0]["source"] or "")
    assert "700" in (edit_rows[0]["source"] or "")


async def test_update_budget_entry_adjusts_project_budget(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    entry = await store.add_budget_entry(
        cfg, "u1", proj["id"], amount=200, source="client deposit",
    )
    # Edit 200 → 150 should drop the project budget by 50 (entry was an
    # over-statement; rolling part of it back).
    ok = await store.update_budget_entry(
        cfg, "u1", entry["id"], amount=150, source="client deposit (corrected)",
    )
    assert ok is True
    refreshed_project = await store.get_project(cfg, "u1", proj["id"])
    assert refreshed_project["budget"] == 650  # was 500 + 200, now 500 + 150
    refreshed_entry = await store.get_budget_entry(cfg, "u1", entry["id"])
    assert refreshed_entry["amount"] == 150
    assert refreshed_entry["source"] == "client deposit (corrected)"


async def test_delete_budget_entry_rolls_back_project_budget(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    entry = await store.add_budget_entry(cfg, "u1", proj["id"], amount=200)
    ok = await store.delete_budget_entry(cfg, "u1", entry["id"])
    assert ok is True
    refreshed = await store.get_project(cfg, "u1", proj["id"])
    assert refreshed["budget"] == 500, "deleting the top-up must unwind it"
    assert await store.get_budget_entry(cfg, "u1", entry["id"]) is None


def test_budgets_category_is_allowed_by_default():
    """Pins the Telegram fix: the 'budgets' category must default to ALLOW so
    NL budget commands execute inline instead of stalling on an approval
    prompt over the chat channels."""
    from lazyclaw.permissions.models import ALLOW, DEFAULT_CATEGORY_PERMISSIONS

    assert DEFAULT_CATEGORY_PERMISSIONS.get("budgets") == ALLOW

