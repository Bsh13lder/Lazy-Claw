"""ECO mode settings — CRUD for user eco preferences.

Settings stored in the existing users.settings JSON column
under the "eco" key. No new DB table needed.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Valid ECO modes
VALID_MODES = {"eco", "hybrid", "full"}

# Default eco settings
DEFAULT_ECO = {
    "mode": "full",
    "show_badges": True,
    "monthly_paid_budget": 0,
    "locked_provider": None,
    "allowed_providers": None,
    "task_overrides": None,
}


async def get_eco_settings(config: Config, user_id: str) -> dict:
    """Get user's ECO mode settings."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_ECO)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_ECO)

    eco = settings.get("eco", {})
    if not isinstance(eco, dict):
        return dict(DEFAULT_ECO)

    # Merge with defaults for any missing keys
    merged = dict(DEFAULT_ECO)
    merged.update(eco)
    return merged


async def update_eco_settings(config: Config, user_id: str, updates: dict) -> dict:
    """Update user's ECO mode settings. Returns the new settings."""
    # Validate mode if provided
    if "mode" in updates:
        if updates["mode"] not in VALID_MODES:
            raise ValueError(f"Invalid eco mode: {updates['mode']}. Must be one of: {VALID_MODES}")

    # Validate locked_provider if provided
    valid_providers = {"groq", "gemini", "openrouter", "together", "mistral", "huggingface", "ollama"}
    if "locked_provider" in updates and updates["locked_provider"] is not None:
        if updates["locked_provider"] not in valid_providers:
            raise ValueError(f"Invalid provider: {updates['locked_provider']}")

    # Validate allowed_providers if provided
    if "allowed_providers" in updates and updates["allowed_providers"] is not None:
        if not isinstance(updates["allowed_providers"], list):
            raise ValueError("allowed_providers must be a list")
        invalid = set(updates["allowed_providers"]) - valid_providers
        if invalid:
            raise ValueError(f"Invalid providers: {invalid}")

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

    # Update eco section
    eco = current_settings.get("eco", dict(DEFAULT_ECO))
    if not isinstance(eco, dict):
        eco = dict(DEFAULT_ECO)

    for key, value in updates.items():
        if key in DEFAULT_ECO:
            eco[key] = value

    # Write back (immutable pattern — new dict)
    new_settings = dict(current_settings)
    new_settings["eco"] = eco
    settings_json = json.dumps(new_settings)

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id),
        )
        await db.commit()

    return eco
