"""Browser persistence settings — stored in users.settings JSON.

Three modes:
  - "off"  — browser launches on-demand, dies after task
  - "auto" — browser stays alive after use, closes after idle timeout
  - "on"   — browser always running, admin keeps it on
"""

from __future__ import annotations

import json
import logging
import time

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_MODES = {"off", "auto", "on"}

DEFAULT_BROWSER = {
    "persistent": "auto",      # "off" | "auto" | "on"
    "idle_timeout": 3600,      # seconds before auto-close (auto mode), 1 hour
    "cdp_approved": False,     # user approved auto-restart Brave with CDP
}

# In-memory activity tracker (no DB overhead)
_last_browser_activity: float = 0.0


def touch_browser_activity() -> None:
    """Mark browser as recently used. Called by browser skills."""
    global _last_browser_activity
    _last_browser_activity = time.monotonic()


def browser_idle_seconds() -> float:
    """Seconds since last browser activity."""
    if _last_browser_activity == 0.0:
        return float("inf")
    return time.monotonic() - _last_browser_activity


async def get_browser_settings(config: Config, user_id: str) -> dict:
    """Get user's browser persistence settings."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_BROWSER)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_BROWSER)

    browser = settings.get("browser", {})
    if not isinstance(browser, dict):
        return dict(DEFAULT_BROWSER)

    merged = dict(DEFAULT_BROWSER)
    merged.update(browser)

    # Backwards compat: old boolean persistent → new mode string
    if isinstance(merged["persistent"], bool):
        merged["persistent"] = "on" if merged["persistent"] else "off"

    return merged


async def update_browser_settings(
    config: Config, user_id: str, updates: dict,
) -> dict:
    """Update user's browser settings. Returns the new settings."""
    if "persistent" in updates:
        mode = updates["persistent"]
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid browser mode: {mode}. Must be one of: {VALID_MODES}"
            )

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

    # Update browser section
    browser = current_settings.get("browser", dict(DEFAULT_BROWSER))
    if not isinstance(browser, dict):
        browser = dict(DEFAULT_BROWSER)

    for key, value in updates.items():
        if key in DEFAULT_BROWSER:
            browser[key] = value

    # Write back (immutable pattern)
    new_settings = dict(current_settings)
    new_settings["browser"] = browser
    settings_json = json.dumps(new_settings)

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id),
        )
        await db.commit()

    return browser
