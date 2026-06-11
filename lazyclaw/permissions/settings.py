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
        logger.warning("Failed to parse permission settings JSON, returning defaults")
        return dict(DEFAULT_PERMISSIONS)

    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        return dict(DEFAULT_PERMISSIONS)

    # Merge with defaults for any missing keys. category_defaults and
    # skill_overrides are deep-merged so that newly added defaults
    # (e.g. a new category in DEFAULT_CATEGORY_PERMISSIONS) surface in
    # the API response for users whose stored dict was saved before the
    # default was introduced. Existing user overrides always win.
    merged = dict(DEFAULT_PERMISSIONS)
    for key in DEFAULT_PERMISSIONS:
        if key not in perms:
            continue
        stored = perms[key]
        if key == "category_defaults" and isinstance(stored, dict):
            merged[key] = {**DEFAULT_CATEGORY_PERMISSIONS, **stored}
        elif key == "skill_overrides" and isinstance(stored, dict):
            merged[key] = dict(stored)
        else:
            merged[key] = stored
    return merged


async def apply_permission_change(
    config: Config, user_id: str, target: str, level: str,
) -> dict:
    """Set a category or skill permission level. Pure helper shared by
    every surface (CLI, Telegram, future web shortcuts). Detects whether
    ``target`` is a known category or a skill override and persists the
    minimal change.

    Returns ``{"target": str, "kind": "category" | "skill", "level": str}``.
    Raises ValueError if ``level`` is not one of allow/ask/deny.
    """
    if level not in VALID_LEVELS:
        raise ValueError(
            f"Invalid permission level: {level}. Must be one of: {sorted(VALID_LEVELS)}"
        )

    settings = await get_permission_settings(config, user_id)

    if target in DEFAULT_CATEGORY_PERMISSIONS:
        cat_defaults = dict(settings.get("category_defaults", {}))
        cat_defaults[target] = level
        await update_permission_settings(
            config, user_id, {"category_defaults": cat_defaults},
        )
        await _audit_permission_change(config, user_id, target, level)
        return {"target": target, "kind": "category", "level": level}

    overrides = dict(settings.get("skill_overrides", {}))
    overrides[target] = level
    await update_permission_settings(
        config, user_id, {"skill_overrides": overrides},
    )
    await _audit_permission_change(config, user_id, target, level)
    return {"target": target, "kind": "skill", "level": level}


async def _audit_permission_change(
    config: Config, user_id: str, target: str, level: str,
) -> None:
    """Best-effort audit write — never breaks the permission change."""
    try:
        from lazyclaw.permissions.audit import log_action

        await log_action(
            config, user_id, "permission_changed",
            skill_name=target,
            arguments={"level": level},
            source="channel",
        )
    except Exception:
        logger.debug("permission-change audit swallowed", exc_info=True)


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
            logger.warning("Failed to parse permission settings JSON during update, starting fresh")
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
