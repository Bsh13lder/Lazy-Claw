"""Persistent-list skills — append/check-off items on a named running list.

The user's running lists ("Grocery", "Home", "Shopping") live as ONE task per
list, with each item as a checkable sub-step (`steps[]`). This replaces the old
behaviour where "add milk to my grocery list" spawned a fresh `add_task` per
item and accidentally tripped the project-materialization threshold.

`add_to_list` finds-or-creates ONE private project + ONE list task and appends
deduped items. `check_off_list_item` ticks an item off via the existing
`toggle_step` store function. Channel-agnostic — identical in Telegram, Web UI
chat, and CLI.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


def _list_task_title(list_name: str) -> str:
    """Canonical title for a list's backing task, e.g. 'Grocery list'."""
    return f"{list_name} list"


def _find_list_task(tasks: list[dict], list_name: str) -> dict | None:
    """Find the single open task that backs a named list.

    Matches an open task (already filtered by the caller) whose ``category``
    equals the list name (case-insensitive). Failing that, falls back to a
    title that *is* the list name as a leading whole word — "Grocery list" and
    "Grocery for this week" resolve, but "Homework prep" / "Gymnastics" do NOT.
    A loose ``key in title`` substring match would wrongly attach items to those
    unrelated tasks (e.g. "Home" → "Homework prep") on the first add, before the
    canonical list task exists. The category match wins; it's the authoritative
    link.
    """
    key = (list_name or "").strip().casefold()
    if not key:
        return None
    for t in tasks:
        if (t.get("category") or "").strip().casefold() == key:
            return t
    prefix = f"{key} "
    for t in tasks:
        title = (t.get("title") or "").strip().casefold()
        if title == key or title.startswith(prefix):
            return t
    return None


def _find_step_by_title(steps: list[dict], item: str) -> dict | None:
    """Fuzzy-match a step by its title (exact case-insensitive, then contains)."""
    needle = (item or "").strip().casefold()
    if not needle:
        return None
    for s in steps:
        if (s.get("title") or "").strip().casefold() == needle:
            return s
    for s in steps:
        title = (s.get("title") or "").strip().casefold()
        if title and (needle in title or title in needle):
            return s
    return None


def _clean_items(items) -> list[str]:
    """Coerce the ``items`` param to a list of trimmed non-empty strings."""
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for raw in items:
        text = (raw or "").strip() if isinstance(raw, str) else str(raw or "").strip()
        if text:
            out.append(text)
    return out


class AddToListSkill(BaseSkill):
    """Append items to a persistent named list (grocery / home / shopping)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_to_list"

    @property
    def description(self) -> str:
        return (
            "Add one or more items to a persistent named list (e.g. grocery, "
            "shopping, home). Use this — NOT add_task — for 'add milk to my "
            "grocery list', 'we're out of eggs', 'put batteries on the shopping "
            "list', 'add bread and butter to home list'. It finds-or-creates a "
            "single list task and appends each item as a checkable sub-step, "
            "deduping items already on the list. NEVER create a separate task "
            "per item. Pass `list` as the list name (e.g. 'Grocery') and `items` "
            "as the array of short item names (e.g. ['milk','eggs'])."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "list": {
                    "type": "string",
                    "description": (
                        "List name, e.g. 'Grocery', 'Shopping', 'Home'. "
                        "Capitalize naturally."
                    ),
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Short item names to add, e.g. ['milk','eggs','bread']. "
                        "One entry per item — never cram several into one string."
                    ),
                },
            },
            "required": ["list", "items"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.budgets import store as budget_store
        from lazyclaw.tasks.store import append_steps, create_task, list_tasks

        list_name = (params.get("list") or "").strip()
        if not list_name:
            return "Which list? Tell me the list name (e.g. 'Grocery')."

        items = _clean_items(params.get("items"))
        if not items:
            return f"No items given to add to the {list_name} list."

        # 1. Ensure the project exists immediately (no 3-task threshold wait),
        #    so the list lives in one private project from item #1.
        try:
            project = await budget_store.get_project_by_name(
                self._config, user_id, list_name,
            )
            if project is None:
                project = await budget_store.create_project(
                    self._config, user_id, list_name,
                    description=f"Running {list_name.lower()} list.",
                )
        except Exception:
            logger.exception("add_to_list: failed to ensure project %r", list_name)
            return f"Couldn't set up the {list_name} list. Please try again."

        # 2. Find the single open list task, or create exactly one.
        try:
            open_tasks = await list_tasks(self._config, user_id, status="todo")
            open_tasks += await list_tasks(
                self._config, user_id, status="in_progress",
            )
            list_task = _find_list_task(open_tasks, list_name)
            if list_task is None:
                list_task = await create_task(
                    self._config, user_id,
                    title=_list_task_title(list_name),
                    category=list_name,
                    owner="user",
                )
        except Exception:
            logger.exception("add_to_list: failed to resolve list task %r", list_name)
            return f"Couldn't open the {list_name} list. Please try again."

        # 3. Append the items (deduped) as sub-steps.
        try:
            before = list_task.get("steps")
            existing_titles = _existing_step_titles(before)
            new_steps = await append_steps(
                self._config, user_id, list_task["id"], items,
            )
        except Exception:
            logger.exception("add_to_list: failed to append items to %r", list_name)
            return f"Couldn't add the items to the {list_name} list. Please try again."

        if new_steps is None:
            return f"The {list_name} list went missing. Please try again."

        added = [it for it in items if it.strip().casefold() not in existing_titles]
        return _format_add_result(list_name, added, items, len(new_steps))


def _existing_step_titles(raw_steps) -> set[str]:
    """Case-folded titles already present on a task's steps column.

    ``raw_steps`` is the (still-encrypted-then-decrypted) steps value carried on
    the task dict returned by the store — a JSON string or already-parsed list.
    Falls back to empty on any parse trouble so a fresh task reports nothing.
    """
    from lazyclaw.tasks.store import _normalize_steps, _parse_steps

    try:
        parsed = _parse_steps(raw_steps) if isinstance(raw_steps, str) else raw_steps
        normalized = _normalize_steps(parsed)
    except Exception:
        return set()
    return {(s.get("title") or "").strip().casefold() for s in normalized}


def _format_add_result(
    list_name: str, added: list[str], requested: list[str], total: int,
) -> str:
    """Build the short confirmation line for add_to_list."""
    if not added:
        dupes = ", ".join(requested)
        return f"Already on the {list_name} list: {dupes}."
    added_str = ", ".join(added)
    return f"Added {added_str} to {list_name} — {total} items."


class CheckOffListItemSkill(BaseSkill):
    """Tick an item off a persistent named list."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "check_off_list_item"

    @property
    def description(self) -> str:
        return (
            "Check an item off a persistent named list (grocery / shopping / "
            "home). Use this for 'got the milk', 'bought the eggs', 'I have "
            "the bread now' when the item is on a known list. Pass `list` (the "
            "list name) and `item` (the item to tick off). It fuzzy-matches the "
            "item and flips it done — it does NOT remove it."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "list": {
                    "type": "string",
                    "description": "List name, e.g. 'Grocery'.",
                },
                "item": {
                    "type": "string",
                    "description": "The item to tick off, e.g. 'milk'.",
                },
            },
            "required": ["list", "item"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import (
            _normalize_steps,
            _parse_steps,
            list_tasks,
            toggle_step,
        )

        list_name = (params.get("list") or "").strip()
        item = (params.get("item") or "").strip()
        if not list_name:
            return "Which list? Tell me the list name (e.g. 'Grocery')."
        if not item:
            return "Which item should I check off?"

        try:
            open_tasks = await list_tasks(self._config, user_id, status="todo")
            open_tasks += await list_tasks(
                self._config, user_id, status="in_progress",
            )
        except Exception:
            logger.exception("check_off_list_item: failed to list tasks")
            return f"Couldn't open the {list_name} list. Please try again."

        list_task = _find_list_task(open_tasks, list_name)
        if list_task is None:
            return f"No {list_name} list found yet."

        steps = _normalize_steps(_parse_steps(list_task.get("steps")))
        step = _find_step_by_title(steps, item)
        if step is None:
            return f"'{item}' isn't on the {list_name} list."

        try:
            updated = await toggle_step(
                self._config, user_id, list_task["id"], step["id"],
            )
        except Exception:
            logger.exception("check_off_list_item: toggle failed")
            return f"Couldn't check off '{item}'. Please try again."

        if updated is None:
            return f"Couldn't check off '{item}' — the list changed. Please try again."
        return f"Checked off {step['title']} on the {list_name} list."
