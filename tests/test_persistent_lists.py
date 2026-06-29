"""Tests for Feature B — Persistent Lists.

Covers the 2026-06-25 list pass:
- ``tasks/store.append_steps`` appends new items, dedups case-insensitively,
  no-ops on full-duplicate, returns None for a missing task, leaves other task
  fields untouched.
- ``add_to_list`` creates exactly ONE project + ONE list task on first call,
  then appends to the SAME task on a second call (no second project/task), with
  dedup honored.
- ``check_off_list_item`` flips the matching step's ``done`` flag; an unknown
  item leaves the list unmutated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import store as budget_store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.skills.builtin.list_manager import (
    AddToListSkill,
    CheckOffListItemSkill,
)
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


def _titles(steps: list[dict] | None) -> list[str]:
    return [s["title"] for s in steps or []]


# ── append_steps ──────────────────────────────────────────────────────────


async def test_append_steps_adds_new_items(cfg):
    t = await task_store.create_task(cfg, "u1", "Grocery list")
    steps = await task_store.append_steps(cfg, "u1", t["id"], ["milk", "eggs"])
    assert steps is not None
    assert _titles(steps) == ["milk", "eggs"]
    assert all(s["done"] is False for s in steps)
    assert all(s["id"] for s in steps)  # ids assigned


async def test_append_steps_dedups_case_insensitive(cfg):
    t = await task_store.create_task(
        cfg, "u1", "Grocery list", steps=[{"title": "Milk"}],
    )
    steps = await task_store.append_steps(
        cfg, "u1", t["id"], ["  milk ", "MILK", "eggs"],
    )
    # Only one "Milk" survives (original casing kept); "eggs" added once.
    assert _titles(steps) == ["Milk", "eggs"]


async def test_append_steps_noop_when_all_duplicates(cfg):
    t = await task_store.create_task(
        cfg, "u1", "Grocery list", steps=[{"title": "milk"}, {"title": "eggs"}],
    )
    steps = await task_store.append_steps(cfg, "u1", t["id"], ["MILK", "Eggs"])
    assert _titles(steps) == ["milk", "eggs"]


async def test_append_steps_returns_none_for_missing_task(cfg):
    assert await task_store.append_steps(cfg, "u1", "no-such-id", ["x"]) is None


async def test_append_steps_leaves_other_fields_untouched(cfg):
    t = await task_store.create_task(
        cfg, "u1", "Grocery list",
        category="Grocery", priority="high", description="weekly run",
    )
    await task_store.append_steps(cfg, "u1", t["id"], ["milk"])
    after = await task_store.get_task(cfg, "u1", t["id"])
    assert after is not None
    assert after["title"] == "Grocery list"
    assert after["category"] == "Grocery"
    assert after["priority"] == "high"
    assert after["description"] == "weekly run"


async def test_append_steps_skips_blank_items(cfg):
    t = await task_store.create_task(cfg, "u1", "Grocery list")
    steps = await task_store.append_steps(cfg, "u1", t["id"], ["", "  ", "milk"])
    assert _titles(steps) == ["milk"]


# ── add_to_list skill ─────────────────────────────────────────────────────


async def test_add_to_list_creates_one_project_and_one_task(cfg):
    skill = AddToListSkill(config=cfg)
    msg = await skill.execute("u1", {"list": "Grocery", "items": ["milk", "eggs"]})
    assert "milk" in msg and "Grocery" in msg

    # Exactly one project named Grocery.
    proj = await budget_store.get_project_by_name(cfg, "u1", "Grocery")
    assert proj is not None
    assert len([p for p in await budget_store.list_projects(cfg, "u1")]) == 1

    # Exactly one open task, with the items as steps.
    tasks = await task_store.list_tasks(cfg, "u1", status="todo")
    tasks += await task_store.list_tasks(cfg, "u1", status="in_progress")
    list_tasks = [t for t in tasks if (t.get("category") or "") == "Grocery"]
    assert len(list_tasks) == 1
    steps = task_store._normalize_steps(
        task_store._parse_steps(list_tasks[0].get("steps"))
    )
    assert _titles(steps) == ["milk", "eggs"]


async def test_add_to_list_appends_to_same_task_no_new_project(cfg):
    skill = AddToListSkill(config=cfg)
    await skill.execute("u1", {"list": "Grocery", "items": ["milk"]})
    await skill.execute("u1", {"list": "Grocery", "items": ["eggs", "bread"]})

    # No second project.
    assert len(await budget_store.list_projects(cfg, "u1")) == 1

    # No second task — still exactly one open Grocery task.
    tasks = await task_store.list_tasks(cfg, "u1", status="todo")
    tasks += await task_store.list_tasks(cfg, "u1", status="in_progress")
    grocery = [t for t in tasks if (t.get("category") or "") == "Grocery"]
    assert len(grocery) == 1

    steps = task_store._normalize_steps(
        task_store._parse_steps(grocery[0].get("steps"))
    )
    assert _titles(steps) == ["milk", "eggs", "bread"]


async def test_add_to_list_dedups_existing_items(cfg):
    skill = AddToListSkill(config=cfg)
    await skill.execute("u1", {"list": "Grocery", "items": ["milk"]})
    msg = await skill.execute("u1", {"list": "Grocery", "items": ["MILK"]})
    assert "Already on the Grocery list" in msg

    tasks = await task_store.list_tasks(cfg, "u1", status="todo")
    grocery = [t for t in tasks if (t.get("category") or "") == "Grocery"]
    steps = task_store._normalize_steps(
        task_store._parse_steps(grocery[0].get("steps"))
    )
    assert _titles(steps) == ["milk"]


async def test_add_to_list_requires_items(cfg):
    skill = AddToListSkill(config=cfg)
    msg = await skill.execute("u1", {"list": "Grocery", "items": []})
    assert "No items" in msg
    assert await budget_store.get_project_by_name(cfg, "u1", "Grocery") is None


async def test_add_to_list_ignores_substring_title_collision(cfg):
    """Items must NOT attach to an unrelated task whose title merely contains
    the list name (e.g. 'Home' must not land on 'Homework prep')."""
    await task_store.create_task(cfg, "u1", "Homework prep")

    skill = AddToListSkill(config=cfg)
    await skill.execute("u1", {"list": "Home", "items": ["batteries"]})

    tasks = await task_store.list_tasks(cfg, "u1", status="todo")
    homework = [t for t in tasks if t["title"] == "Homework prep"][0]
    assert _titles(
        task_store._normalize_steps(task_store._parse_steps(homework.get("steps")))
    ) == []  # the unrelated task got nothing

    home = [t for t in tasks if (t.get("category") or "") == "Home"]
    assert len(home) == 1  # a dedicated 'Home list' task was created instead
    assert _titles(
        task_store._normalize_steps(task_store._parse_steps(home[0].get("steps")))
    ) == ["batteries"]


# ── check_off_list_item skill ─────────────────────────────────────────────


async def test_check_off_flips_done(cfg):
    add = AddToListSkill(config=cfg)
    await add.execute("u1", {"list": "Grocery", "items": ["milk", "eggs"]})

    check = CheckOffListItemSkill(config=cfg)
    msg = await check.execute("u1", {"list": "Grocery", "item": "milk"})
    assert "milk" in msg.lower()

    tasks = await task_store.list_tasks(cfg, "u1", status="todo")
    grocery = [t for t in tasks if (t.get("category") or "") == "Grocery"][0]
    steps = task_store._normalize_steps(
        task_store._parse_steps(grocery.get("steps"))
    )
    by_title = {s["title"]: s["done"] for s in steps}
    assert by_title["milk"] is True
    assert by_title["eggs"] is False


async def test_check_off_unknown_item_no_mutation(cfg):
    add = AddToListSkill(config=cfg)
    await add.execute("u1", {"list": "Grocery", "items": ["milk"]})

    check = CheckOffListItemSkill(config=cfg)
    msg = await check.execute("u1", {"list": "Grocery", "item": "caviar"})
    assert "isn't on the Grocery list" in msg

    tasks = await task_store.list_tasks(cfg, "u1", status="todo")
    grocery = [t for t in tasks if (t.get("category") or "") == "Grocery"][0]
    steps = task_store._normalize_steps(
        task_store._parse_steps(grocery.get("steps"))
    )
    assert all(s["done"] is False for s in steps)


async def test_check_off_unknown_list(cfg):
    check = CheckOffListItemSkill(config=cfg)
    msg = await check.execute("u1", {"list": "Nope", "item": "milk"})
    assert "No Nope list found" in msg
