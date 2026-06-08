"""Tests for task-category ⇄ project materialization + rename consistency.

Covers the 2026-06-02 fix bundle for the "errands" project bug:
- ``list_categories`` / ``count_tasks_in_category`` helpers.
- ``rename_category`` re-points encrypted task categories.
- ``create_task`` materializes a first-class project once a category crosses
  the threshold (and not before).
- ``update_project`` rename re-points every task's category (no orphans).
- ``_ensure_project_note`` leads with the project description.
- ``CreateProjectSkill`` materializes a real project + wikilink page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import store as budget_store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store

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


# ── category helpers ──────────────────────────────────────────────────────


async def test_list_categories_distinct_first_seen_casing(cfg):
    await task_store.create_task(cfg, "u1", "a", category="Home")
    await task_store.create_task(cfg, "u1", "b", category="home")  # dup casefold
    await task_store.create_task(cfg, "u1", "c", category="Work")
    await task_store.create_task(cfg, "u1", "d")  # no category

    cats = await task_store.list_categories(cfg, "u1")
    # Casefold-deduped: "Home"/"home" collapse to one entry; empty skipped.
    assert sorted(c.casefold() for c in cats) == ["home", "work"]
    assert len(cats) == 2


async def test_count_tasks_in_category_casefold(cfg):
    await task_store.create_task(cfg, "u1", "a", category="Groceries")
    await task_store.create_task(cfg, "u1", "b", category="groceries")
    assert await task_store.count_tasks_in_category(cfg, "u1", "GROCERIES") == 2
    assert await task_store.count_tasks_in_category(cfg, "u1", "nope") == 0
    assert await task_store.count_tasks_in_category(cfg, "u1", "") == 0


async def test_rename_category_repoints_tasks(cfg):
    t1 = await task_store.create_task(cfg, "u1", "a", category="Old")
    t2 = await task_store.create_task(cfg, "u1", "b", category="old")
    await task_store.create_task(cfg, "u1", "c", category="Other")

    n = await task_store.rename_category(cfg, "u1", "Old", "New")
    assert n == 2

    assert (await task_store.get_task(cfg, "u1", t1["id"]))["category"] == "New"
    assert (await task_store.get_task(cfg, "u1", t2["id"]))["category"] == "New"
    # Untouched category stays put.
    cats = await task_store.list_categories(cfg, "u1")
    assert "New" in cats and "Other" in cats and "Old" not in cats


async def test_rename_category_noop_when_equivalent(cfg):
    await task_store.create_task(cfg, "u1", "a", category="Same")
    assert await task_store.rename_category(cfg, "u1", "Same", " same ") == 0
    assert await task_store.rename_category(cfg, "u1", "", "x") == 0


# ── threshold materialization ─────────────────────────────────────────────


async def test_category_materializes_at_threshold(cfg):
    # Below threshold → phantom (no projects row).
    await task_store.create_task(cfg, "u1", "g1", category="Errands")
    await task_store.create_task(cfg, "u1", "g2", category="Errands")
    assert await budget_store.get_project_by_name(cfg, "u1", "Errands") is None

    # Crossing the threshold materializes a real project + wikilink page.
    await task_store.create_task(cfg, "u1", "g3", category="Errands")
    proj = await budget_store.get_project_by_name(cfg, "u1", "Errands")
    assert proj is not None
    assert proj["lazybrain_note_id"]


async def test_one_off_category_stays_phantom(cfg):
    await task_store.create_task(cfg, "u1", "solo", category="Misc")
    assert await budget_store.get_project_by_name(cfg, "u1", "Misc") is None


async def test_no_category_never_materializes(cfg):
    for i in range(4):
        await task_store.create_task(cfg, "u1", f"t{i}")
    projects = await budget_store.list_projects(cfg, "u1")
    assert projects == []


# ── project rename re-points tasks (no orphans) ───────────────────────────


async def test_update_project_rename_repoints_tasks(cfg):
    proj = await budget_store.create_project(cfg, "u1", "Foo")
    t = await task_store.create_task(cfg, "u1", "task", category="Foo")

    ok = await budget_store.update_project(cfg, "u1", proj["id"], name="Bar")
    assert ok

    # Project row renamed.
    assert await budget_store.get_project_by_name(cfg, "u1", "Bar") is not None
    assert await budget_store.get_project_by_name(cfg, "u1", "Foo") is None
    # Task followed the rename — no orphan.
    assert (await task_store.get_task(cfg, "u1", t["id"]))["category"] == "Bar"


# ── description on the wikilink page ──────────────────────────────────────


async def test_project_note_includes_description(cfg):
    proj = await budget_store.create_project(
        cfg, "u1", "Reno", description="Kitchen remodel — tiles, paint, sink.",
    )
    from lazyclaw.lazybrain import store as lb_store

    note = await lb_store.get_note(cfg, "u1", proj["lazybrain_note_id"])
    assert "Kitchen remodel" in note["content"]
    assert "# Reno Project" in note["content"]


# ── CreateProjectSkill ────────────────────────────────────────────────────


async def test_create_project_skill_materializes(cfg):
    from lazyclaw.skills.builtin.budget_manager import CreateProjectSkill

    skill = CreateProjectSkill(config=cfg)
    msg = await skill.execute("u1", {
        "project": "Private",
        "description": "Personal errands and home stuff.",
    })
    assert "Private" in msg and "created" in msg.lower()

    proj = await budget_store.get_project_by_name(cfg, "u1", "Private")
    assert proj is not None
    assert proj["description"] == "Personal errands and home stuff."
    assert proj["lazybrain_note_id"]


async def test_create_project_skill_idempotent(cfg):
    from lazyclaw.skills.builtin.budget_manager import CreateProjectSkill

    skill = CreateProjectSkill(config=cfg)
    await skill.execute("u1", {"project": "Dup"})
    msg = await skill.execute("u1", {"project": "  dup "})
    assert "already exists" in msg.lower()
    projects = [p for p in await budget_store.list_projects(cfg, "u1")
                if p["name_key"] == "dup"]
    assert len(projects) == 1
