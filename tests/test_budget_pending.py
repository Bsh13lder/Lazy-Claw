"""Tests for the Telegram expense-choice TTL cache + skill round-trip.

The Telegram inline-button flow is:
  1. add_expense can't unambiguously resolve a project/task → it stashes the
     in-flight amount + candidates in ``budgets.pending`` and returns text.
  2. The Telegram outbound layer attaches an inline keyboard built from the
     pending entry.
  3. User taps a button → the callback handler re-fires the expense with the
     chosen value and clears pending state.

These tests cover the cache behavior + the skill stashing path (the actual
Telegram render/callback is integration-tested at deploy time).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lazyclaw.budgets import pending, store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _wipe_pending_between_tests():
    """The pending cache is module-level state; isolate each test."""
    pending._PENDING.clear()
    yield
    pending._PENDING.clear()


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


async def test_set_get_clear_pending(cfg):
    """Basic round-trip; the cache returns the entry verbatim."""
    entry = pending.PendingExpenseChoice(
        user_id="u1", amount=40.0, currency="EUR",
        description="hosting", vendor=None, spent_at=None,
        task_name=None, kind="project",
        candidates=(("p1", "nima"), ("p2", "ninja")),
        project_id=None, project_name=None,
        token=pending.new_token(),
    )
    pending.set_pending(entry)
    got = pending.get_pending("u1")
    assert got is entry
    pending.clear_pending("u1")
    assert pending.get_pending("u1") is None


async def test_pending_expires_after_ttl():
    entry = pending.PendingExpenseChoice(
        user_id="u1", amount=10.0, currency="EUR",
        description=None, vendor=None, spent_at=None,
        task_name=None, kind="project",
        candidates=(("p1", "nima"),),
        project_id=None, project_name=None,
        token=pending.new_token(),
        created_at=time.time() - (pending._TTL_SECONDS + 10),
    )
    pending.set_pending(entry)
    assert pending.get_pending("u1") is None
    # Side effect: expired entry is pruned from the cache.
    assert "u1" not in pending._PENDING


async def test_set_pending_overwrites_prior_entry():
    """Most-recent capture wins — older pending state is discarded so a stale
    button can't fire a duplicate expense."""
    first_token = pending.new_token()
    pending.set_pending(pending.PendingExpenseChoice(
        user_id="u1", amount=10.0, currency="EUR",
        description="coffee", vendor=None, spent_at=None,
        task_name=None, kind="project",
        candidates=(("p1", "nima"),),
        project_id=None, project_name=None, token=first_token,
    ))
    second_token = pending.new_token()
    pending.set_pending(pending.PendingExpenseChoice(
        user_id="u1", amount=99.0, currency="USD",
        description="lunch", vendor=None, spent_at=None,
        task_name=None, kind="project",
        candidates=(("p2", "ninja"),),
        project_id=None, project_name=None, token=second_token,
    ))
    got = pending.get_pending("u1")
    assert got is not None
    assert got.token == second_token
    assert got.amount == 99.0


async def test_skill_stashes_on_multi_project_match(cfg):
    """When `add_expense` sees multiple project matches it must (a) NOT log
    an expense and (b) leave a pending entry the Telegram layer can render."""
    from lazyclaw.skills.builtin.budget_manager import AddExpenseSkill

    await store.create_project(cfg, "u1", "nima", budget=500)
    await store.create_project(cfg, "u1", "ninja", budget=200)

    skill = AddExpenseSkill(config=cfg)
    reply = await skill.execute(
        "u1", {"project": "ni", "amount": 40, "description": "hosting"},
    )

    # No expense was logged.
    proj_nima = await store.get_project_by_name(cfg, "u1", "nima")
    proj_ninja = await store.get_project_by_name(cfg, "u1", "ninja")
    assert await store.list_expenses(cfg, "u1", project_id=proj_nima["id"]) == []
    assert await store.list_expenses(cfg, "u1", project_id=proj_ninja["id"]) == []

    # Reply asks the user to pick.
    assert "Multiple projects" in reply or "match" in reply.lower()

    # Pending state stashed with both candidates so the keyboard renders.
    entry = pending.get_pending("u1")
    assert entry is not None
    assert entry.kind == "project"
    assert entry.amount == 40
    assert entry.description == "hosting"
    candidate_names = {label for _id, label in entry.candidates}
    assert "nima" in candidate_names and "ninja" in candidate_names


async def test_skill_stashes_when_no_project_no_default(cfg):
    """No project named + no default = stash the top-N recent projects so
    the user can tap one (or send 📥 Inbox)."""
    from lazyclaw.skills.builtin.budget_manager import AddExpenseSkill

    await store.create_project(cfg, "u1", "personal")
    await store.create_project(cfg, "u1", "nima")

    skill = AddExpenseSkill(config=cfg)
    reply = await skill.execute("u1", {"amount": 12, "description": "coffee"})

    assert "Which project" in reply or "?" in reply
    entry = pending.get_pending("u1")
    assert entry is not None
    assert entry.amount == 12
    assert len(entry.candidates) >= 1


async def test_skill_clears_pending_on_successful_log(cfg):
    """Once the resolution actually succeeds, pending state must be cleared
    so a button tap from the prior turn can't fire a duplicate."""
    from lazyclaw.skills.builtin.budget_manager import AddExpenseSkill

    await store.create_project(cfg, "u1", "nima")
    pending.set_pending(pending.PendingExpenseChoice(
        user_id="u1", amount=10.0, currency="EUR",
        description="stale", vendor=None, spent_at=None,
        task_name=None, kind="project",
        candidates=(("x", "x"),),
        project_id=None, project_name=None, token=pending.new_token(),
    ))
    assert pending.get_pending("u1") is not None

    skill = AddExpenseSkill(config=cfg)
    await skill.execute("u1", {"project": "nima", "amount": 5})
    assert pending.get_pending("u1") is None
