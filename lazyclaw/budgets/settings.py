"""Per-user budget settings stored in users.settings JSON under "budgets".

Mirrors lazyclaw.settings.general. Currently holds the default project for
unrouted expenses — used by add_expense to avoid asking back when the user
hasn't named a project on a fresh capture.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


async def get_default_expense_project(config: Config, user_id: str) -> str | None:
    """Return the user's default project name for unrouted expenses, or None."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()
    if not result or not result[0]:
        return None
    try:
        s = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return None
    budgets = s.get("budgets")
    if not isinstance(budgets, dict):
        return None
    v = budgets.get("default_project")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


async def set_default_expense_project(
    config: Config, user_id: str, project_name: str | None,
) -> None:
    """Set or clear (``None`` / empty) the default project for new expenses."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()
    current: dict = {}
    if result and result[0]:
        try:
            current = json.loads(result[0])
        except (json.JSONDecodeError, TypeError):
            current = {}
    budgets = current.get("budgets")
    if not isinstance(budgets, dict):
        budgets = {}
    budgets = dict(budgets)
    cleaned = (project_name or "").strip()
    if cleaned:
        budgets["default_project"] = cleaned
    else:
        budgets.pop("default_project", None)
    new_settings = dict(current)
    new_settings["budgets"] = budgets

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(new_settings), user_id),
        )
        await db.commit()
    logger.debug(
        "default_expense_project for %s -> %s",
        user_id, cleaned or "(cleared)",
    )
