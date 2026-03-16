"""Team settings — CRUD for user team preferences.

Settings stored in the existing users.settings JSON column
under the "teams" key. No new DB table needed.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Valid team modes
VALID_MODES = {"auto", "always", "never"}
VALID_CRITIC_MODES = {"auto", "always", "never"}

# Default team settings
DEFAULT_TEAMS = {
    "mode": "never",
    "critic_mode": "auto",
    "max_parallel": 3,
    "specialist_timeout": 120,
}


async def get_team_settings(config: Config, user_id: str) -> dict:
    """Get user's team mode settings."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_TEAMS)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_TEAMS)

    teams = settings.get("teams", {})
    if not isinstance(teams, dict):
        return dict(DEFAULT_TEAMS)

    merged = dict(DEFAULT_TEAMS)
    merged.update(teams)
    return merged


async def update_team_settings(config: Config, user_id: str, updates: dict) -> dict:
    """Update user's team settings. Returns the new settings."""
    if "mode" in updates:
        if updates["mode"] not in VALID_MODES:
            raise ValueError(f"Invalid team mode: {updates['mode']}. Must be one of: {VALID_MODES}")

    if "critic_mode" in updates:
        if updates["critic_mode"] not in VALID_CRITIC_MODES:
            raise ValueError(f"Invalid critic mode: {updates['critic_mode']}. Must be one of: {VALID_CRITIC_MODES}")

    if "max_parallel" in updates:
        val = updates["max_parallel"]
        if not isinstance(val, int) or val < 1 or val > 10:
            raise ValueError("max_parallel must be an integer between 1 and 10")

    if "specialist_timeout" in updates:
        val = updates["specialist_timeout"]
        if not isinstance(val, int) or val < 10 or val > 600:
            raise ValueError("specialist_timeout must be between 10 and 600 seconds")

    # Load current settings
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    current_settings = {}
    if result and result[0]:
        try:
            current_settings = json.loads(result[0])
        except (json.JSONDecodeError, TypeError):
            current_settings = {}

    # Update teams section
    teams = current_settings.get("teams", dict(DEFAULT_TEAMS))
    if not isinstance(teams, dict):
        teams = dict(DEFAULT_TEAMS)

    for key, value in updates.items():
        if key in DEFAULT_TEAMS:
            teams[key] = value

    # Write back (immutable pattern — new dict)
    new_settings = dict(current_settings)
    new_settings["teams"] = teams
    settings_json = json.dumps(new_settings)

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id),
        )
        await db.commit()

    return teams
