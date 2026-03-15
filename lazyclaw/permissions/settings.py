"""Permission settings — CRUD for user permission preferences.

Settings stored in the existing users.settings JSON column
under the "permissions" key. No new DB table needed.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.permissions.models import DEFAULT_CATEGORY_PERMISSIONS, VALID_LEVELS

logger = logging.getLogger(__name__)

# Default permission settings
DEFAULT_PERMISSIONS: dict = {
    "category_defaults": dict(DEFAULT_CATEGORY_PERMISSIONS),
    "skill_overrides": {},
    "auto_approve_timeout": 300,
    "require_approval_for_heartbeat": True,
}


async def get_permission_settings(config: Config, user_id: str) -> dict:
    """Get user's permission settings from users.settings JSON."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_PERMISSIONS)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_PERMISSIONS)

    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        return dict(DEFAULT_PERMISSIONS)

    # Merge with defaults for any missing keys
    merged = dict(DEFAULT_PERMISSIONS)
    for key in DEFAULT_PERMISSIONS:
        if key in perms:
            merged[key] = perms[key]
    return merged


async def update_permission_settings(
    config: Config, user_id: str, updates: dict
) -> dict:
    """Update user's permission settings. Returns the new settings."""
    # Validate category_defaults if provided
    if "category_defaults" in updates:
        if not isinstance(updates["category_defaults"], dict):
            raise ValueError("category_defaults must be a dict")
        for level in updates["category_defaults"].values():
            if level not in VALID_LEVELS:
                raise ValueError(
                    f"Invalid permission level: {level}. Must be one of: {VALID_LEVELS}"
                )

    # Validate skill_overrides if provided
    if "skill_overrides" in updates:
        if not isinstance(updates["skill_overrides"], dict):
            raise ValueError("skill_overrides must be a dict")
        for level in updates["skill_overrides"].values():
            if level not in VALID_LEVELS:
                raise ValueError(
                    f"Invalid permission level: {level}. Must be one of: {VALID_LEVELS}"
                )

    # Validate auto_approve_timeout if provided
    if "auto_approve_timeout" in updates:
        timeout = updates["auto_approve_timeout"]
        if not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
            raise ValueError("auto_approve_timeout must be an integer between 30 and 3600")

    # Load current settings
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    current_settings: dict = {}
    if result and result[0]:
        try:
            current_settings = json.loads(result[0])
        except (json.JSONDecodeError, TypeError):
            current_settings = {}

    # Update permissions section (immutable pattern — new dict)
    perms = current_settings.get("permissions", dict(DEFAULT_PERMISSIONS))
    if not isinstance(perms, dict):
        perms = dict(DEFAULT_PERMISSIONS)

    new_perms = dict(perms)
    for key, value in updates.items():
        if key in DEFAULT_PERMISSIONS:
            new_perms[key] = value

    new_settings = dict(current_settings)
    new_settings["permissions"] = new_perms
    settings_json = json.dumps(new_settings)

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id),
        )
        await db.commit()

    return new_perms
