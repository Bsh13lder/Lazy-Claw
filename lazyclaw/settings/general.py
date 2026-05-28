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
import re

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_SEARCH_PROVIDERS: frozenset[str] = frozenset({"auto", "brave", "scraper", "duckduckgo"})

# Stale provider names that existed before the 2026-05-02 Brave rewrite.
# Coerce to "auto" on read so old user rows don't break the Settings UI.
_LEGACY_PROVIDERS: frozenset[str] = frozenset({"serper", "serpapi"})

DEFAULT_GENERAL = {
    "search_provider": "auto",
    "show_cost_badges": True,
    # Default lead-time offsets for advance reminders on important tasks
    # (priority high/urgent OR appointment-class title). Each entry is a
    # negative relative offset like "-2h", "-30m", "-1d" — applied to the
    # task's reminder_at to derive pre_reminders entries. Override per-user.
    "reminder_offsets": ["-2h", "-1h"],
    # Auto-save successful multi-step browser flows as templates (upsert by
    # primary host). Off → user must manually save via canvas/chat skill.
    "auto_save_browser_templates": True,
    # IANA timezone for the user. Drives smart_intake, nl_time, the
    # heartbeat nag loop, and recurring task respawn. Default Madrid since
    # that's the primary deploy; settable via Settings UI or NL.
    "timezone": "Europe/Madrid",
    # End-of-day progress summary at 20:00 user-tz. Lists every
    # in_progress task and its day's progress entries. Off-switch for
    # users who don't want a daily push.
    "eod_summary": True,
    # Awake mode — keep the macOS host (and thus the agent) running with the
    # lid closed. The mechanics (caffeinate / pmset) live in the host awake
    # bridge; this block is the *policy* the heartbeat reconciles toward.
    # `enabled` defaults on so the agent keeps working when plugged in (on
    # battery the lid still sleeps — the skill surfaces that). A "nap" sets
    # `suppressed_until` so reconcile leaves the machine asleep until it wakes.
    "awake": {
        "enabled": True,
        "daily_wake_enabled": False,
        "daily_wake_time": "07:00",
        "suppressed_until": None,
    },
}

# Validates "-Xh", "-Xm", "-Xd" with an optional combined form like "-2h30m".
_OFFSET_RE = re.compile(r"^-(?:\d+d)?(?:\d+h)?(?:\d+m)?$")

# Validates a 24-hour clock time "HH:MM" (00:00–23:59), zero-padded or not.
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


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
    # Coerce stale provider names to "auto" — Serper/SerpAPI were removed
    # in the Brave rewrite (2026-05-02). Old rows shouldn't crash the UI.
    if merged.get("search_provider") in _LEGACY_PROVIDERS:
        merged["search_provider"] = "auto"
    # `awake` is nested — merge defaults so partial stored dicts get every key,
    # and never hand back the shared DEFAULT_GENERAL["awake"] reference.
    awake = dict(DEFAULT_GENERAL["awake"])
    stored_awake = general.get("awake")
    if isinstance(stored_awake, dict):
        awake.update(stored_awake)
    merged["awake"] = awake
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

    if "eod_summary" in updates and updates["eod_summary"] is not None:
        clean["eod_summary"] = bool(updates["eod_summary"])

    if (
        "auto_save_browser_templates" in updates
        and updates["auto_save_browser_templates"] is not None
    ):
        clean["auto_save_browser_templates"] = bool(
            updates["auto_save_browser_templates"]
        )

    if "timezone" in updates and updates["timezone"] is not None:
        tz_val = str(updates["timezone"]).strip()
        if not tz_val:
            raise ValueError("timezone must be a non-empty IANA name (e.g. 'Europe/Madrid')")
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            ZoneInfo(tz_val)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                f"Invalid timezone {tz_val!r}. Use an IANA name like 'Europe/Madrid'."
            )
        clean["timezone"] = tz_val

    if "reminder_offsets" in updates and updates["reminder_offsets"] is not None:
        vals = updates["reminder_offsets"]
        if not isinstance(vals, list):
            raise ValueError("reminder_offsets must be a list of strings")
        cleaned: list[str] = []
        for v in vals:
            s = str(v).strip()
            if not _OFFSET_RE.match(s):
                raise ValueError(
                    f"Invalid offset {v!r}. Use '-2h', '-30m', '-1d', '-2h30m', etc."
                )
            cleaned.append(s)
        clean["reminder_offsets"] = cleaned

    if "awake" in updates and updates["awake"] is not None:
        au = updates["awake"]
        if not isinstance(au, dict):
            raise ValueError("awake must be an object")
        clean_awake: dict = {}
        if "enabled" in au and au["enabled"] is not None:
            clean_awake["enabled"] = bool(au["enabled"])
        if "daily_wake_enabled" in au and au["daily_wake_enabled"] is not None:
            clean_awake["daily_wake_enabled"] = bool(au["daily_wake_enabled"])
        if "daily_wake_time" in au and au["daily_wake_time"] is not None:
            t = str(au["daily_wake_time"]).strip()
            if not _HHMM_RE.match(t):
                raise ValueError(
                    f"Invalid daily_wake_time {t!r}. Use HH:MM (00:00–23:59)."
                )
            hh, mm = t.split(":")
            clean_awake["daily_wake_time"] = f"{int(hh):02d}:{int(mm):02d}"
        if "suppressed_until" in au:
            su = au["suppressed_until"]
            if su is None:
                clean_awake["suppressed_until"] = None
            else:
                su_str = str(su).strip()
                try:
                    from datetime import datetime
                    datetime.fromisoformat(su_str.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(
                        f"Invalid suppressed_until {su!r}; want ISO-8601 or null."
                    )
                clean_awake["suppressed_until"] = su_str
        if clean_awake:
            clean["awake"] = clean_awake

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
    prev_awake = general.get("awake") if isinstance(general.get("awake"), dict) else {}
    general = dict(general)
    general.update(clean)
    # awake updates are partial — merge into the existing sub-dict instead of
    # letting general.update() replace the whole thing.
    if "awake" in clean:
        merged_awake = dict(DEFAULT_GENERAL["awake"])
        merged_awake.update(prev_awake)
        merged_awake.update(clean["awake"])
        general["awake"] = merged_awake

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
