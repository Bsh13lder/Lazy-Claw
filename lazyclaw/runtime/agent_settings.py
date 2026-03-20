"""Agent concurrency settings stored in users.settings['agents'] JSON.

Follows the same pattern as teams/settings.py, browser/browser_settings.py,
and llm/eco_settings.py — all use a sub-key of the users.settings column.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

DEFAULT_AGENTS: dict[str, Any] = {
    "auto_delegate": True,
    "max_concurrent_specialists": 3,
    "max_ram_mb": 512,
    "specialist_timeout_s": 120,
}


async def get_agent_settings(config, user_id: str) -> dict:
    """Read agent settings, merged with defaults."""
    try:
        async with db_session(config) as db:
            cursor = await db.execute(
                "SELECT settings FROM users WHERE id = ?", (user_id,),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                settings = json.loads(row[0])
                return {**DEFAULT_AGENTS, **settings.get("agents", {})}
    except Exception as exc:
        logger.debug("get_agent_settings failed: %s", exc)
    return dict(DEFAULT_AGENTS)


async def update_agent_settings(config, user_id: str, updates: dict) -> dict:
    """Validate and update agent settings. Returns new merged settings."""
    if "auto_delegate" in updates:
        if not isinstance(updates["auto_delegate"], bool):
            raise ValueError("auto_delegate must be a boolean")
    if "max_concurrent_specialists" in updates:
        val = int(updates["max_concurrent_specialists"])
        if not 1 <= val <= 10:
            raise ValueError("max_concurrent_specialists must be 1-10")
        updates = {**updates, "max_concurrent_specialists": val}
    if "max_ram_mb" in updates:
        val = int(updates["max_ram_mb"])
        if not 128 <= val <= 4096:
            raise ValueError("max_ram_mb must be 128-4096")
        updates = {**updates, "max_ram_mb": val}
    if "specialist_timeout_s" in updates:
        val = int(updates["specialist_timeout_s"])
        if not 10 <= val <= 600:
            raise ValueError("specialist_timeout_s must be 10-600")
        updates = {**updates, "specialist_timeout_s": val}

    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,),
        )
        row = await cursor.fetchone()
        current = json.loads(row[0]) if row and row[0] else {}

        agents = {**DEFAULT_AGENTS, **current.get("agents", {}), **updates}
        new_settings = dict(current)
        new_settings["agents"] = agents
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(new_settings), user_id),
        )
        await db.commit()
        return agents
