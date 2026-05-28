"""Tests for recurring expenses (cron-driven auto-charge).

Covers: create_recurring_expense provisions an agent_jobs cron row + an
[EXPENSE:<id>] instruction, materialize inserts a posted expense carrying the
recurring_expense_id, and the dedup guard prevents a double-charge in the same
period.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import store
from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt
from lazyclaw.crypto.key_manager import get_user_dek
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


async def test_create_recurring_provisions_cron_job(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    rule = await store.create_recurring_expense(
        cfg, "u1", proj["id"],
        amount=12, cron_expression="0 0 1 * *", description="hosting",
    )
    assert rule["job_id"], "must create a scheduler job"
    assert rule["next_run"], "must compute next_run"

    key = await get_user_dek(cfg, "u1")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT job_type, cron_expression, instruction FROM agent_jobs "
            "WHERE id = ?",
            (rule["job_id"],),
        )
        row = await cur.fetchone()
    job_type, cron_expr, enc_instruction = row
    assert job_type == "cron"
    assert cron_expr == "0 0 1 * *"
    assert decrypt(enc_instruction, key) == f"[EXPENSE:{rule['id']}]"


async def test_materialize_creates_expense_with_recurring_id(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    rule = await store.create_recurring_expense(
        cfg, "u1", proj["id"], amount=12, cron_expression="0 0 1 * *",
    )
    expense = await store.materialize_recurring_expense(cfg, "u1", rule["id"])
    assert expense is not None
    assert expense["recurring_expense_id"] == rule["id"]
    assert expense["amount"] == 12
    assert expense["_project_name"] == "nima"

    refreshed = await store.get_recurring(cfg, "u1", rule["id"])
    assert refreshed["last_charged_at"], "last_charged_at must be stamped"


async def test_materialize_dedup_guard(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    rule = await store.create_recurring_expense(
        cfg, "u1", proj["id"], amount=12, cron_expression="0 0 1 * *",
    )
    first = await store.materialize_recurring_expense(cfg, "u1", rule["id"])
    second = await store.materialize_recurring_expense(cfg, "u1", rule["id"])
    assert first is not None
    assert second is None, "monthly cron must not double-charge in same period"

    expenses = await store.list_expenses(cfg, "u1", project_id=proj["id"])
    assert len(expenses) == 1


async def test_materialize_skips_inactive_rule(cfg):
    proj = await store.create_project(cfg, "u1", "nima", budget=500)
    rule = await store.create_recurring_expense(
        cfg, "u1", proj["id"], amount=12, cron_expression="0 0 1 * *",
    )
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE recurring_expenses SET status = 'paused' WHERE id = ?",
            (rule["id"],),
        )
        await db.commit()
    assert await store.materialize_recurring_expense(cfg, "u1", rule["id"]) is None
