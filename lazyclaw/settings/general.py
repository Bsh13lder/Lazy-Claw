"""General per-user settings (non-ECO).

Lives in the existing ``users.settings`` JSON column under the ``"general"`` key,
mirroring ``lazyclaw.llm.eco_settings``. No new tables.

NOTE: ``show_cost_badges`` is a mirror of the legacy ``eco.show_badges`` flag —
we intentionally write both on update so older code paths keep working. Do not
remove "the redundant one" without auditing every reader.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_SEARCH_PROVIDERS: frozenset[str] = frozenset({"serper", "serpapi", "duckduckgo", "auto"})

DEFAULT_GENERAL = {
    "search_provider": "auto",
    "show_cost_badges": True,
}


async def get_general_settings(config: Config, user_id: str) -> dict:
    """Fetch a user's general settings (merged with defaults)."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_GENERAL)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_GENERAL)

    general = settings.get("general", {})
    if not isinstance(general, dict):
        general = {}

    merged = dict(DEFAULT_GENERAL)
    merged.update(general)
    return merged


async def update_general_settings(
    config: Config, user_id: str, updates: dict
) -> dict:
    """Update a user's general settings. Returns the new merged settings."""
    clean: dict = {}

    if "search_provider" in updates and updates["search_provider"] is not None:
        val = str(updates["search_provider"]).lower().strip()
        if val not in VALID_SEARCH_PROVIDERS:
            raise ValueError(
                f"Invalid search_provider: {updates['search_provider']}. "
                f"Use one of: {sorted(VALID_SEARCH_PROVIDERS)}"
            )
        clean["search_provider"] = val

    if "show_cost_badges" in updates and updates["show_cost_badges"] is not None:
        clean["show_cost_badges"] = bool(updates["show_cost_badges"])

    if not clean:
        return await get_general_settings(config, user_id)

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

    general = current.get("general", {})
    if not isinstance(general, dict):
        general = {}
    general = dict(general)
    general.update(clean)

    # Mirror show_cost_badges into the legacy eco.show_badges flag so the ECO
    # tab and the cost-badge renderer keep agreeing.
    if "show_cost_badges" in clean:
        eco = current.get("eco", {})
        if not isinstance(eco, dict):
            eco = {}
        eco = dict(eco)
        eco["show_badges"] = clean["show_cost_badges"]
        new_settings = dict(current)
        new_settings["eco"] = eco
    else:
        new_settings = dict(current)

    new_settings["general"] = general

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(new_settings), user_id),
        )
        await db.commit()

    merged = dict(DEFAULT_GENERAL)
    merged.update(general)
    return merged
