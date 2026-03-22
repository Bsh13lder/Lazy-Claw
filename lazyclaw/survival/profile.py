"""User's freelance skills profile for job matching.

Stored in users.settings JSON under the "survival" key,
following the same pattern as eco_settings, browser_settings, etc.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session


@dataclass(frozen=True)
class SkillsProfile:
    """Immutable freelance profile used for job matching."""

    skills: tuple[str, ...] = ()
    title: str = ""
    bio: str = ""
    min_hourly_rate: float = 0.0
    min_fixed_rate: float = 0.0
    max_concurrent_jobs: int = 2
    platforms: tuple[str, ...] = ()
    preferred_categories: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    work_hours: str = "flexible"


DEFAULT_PROFILE = SkillsProfile()

_PROFILE_FIELDS = frozenset(SkillsProfile.__dataclass_fields__.keys())

# Explicit list — safer than annotation introspection
_TUPLE_FIELDS: frozenset[str] = frozenset({
    "skills", "platforms", "preferred_categories", "excluded_keywords",
})

_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "min_hourly_rate", "min_fixed_rate",
})


def _coerce_updates(updates: dict[str, object]) -> dict[str, object] | str:
    """Validate and coerce update values. Returns error string on failure."""
    result: dict[str, object] = {}
    for k, v in updates.items():
        if k not in _PROFILE_FIELDS:
            continue
        if k in _NUMERIC_FIELDS:
            try:
                result[k] = float(str(v).lstrip("$").strip())
            except (ValueError, TypeError):
                return f"Invalid value for {k}: must be a number."
        elif k in _TUPLE_FIELDS:
            if isinstance(v, (list, tuple)):
                result[k] = list(v)
            else:
                result[k] = [str(v)]
        else:
            result[k] = v
    return result


async def get_profile(config: Config, user_id: str) -> SkillsProfile:
    """Load skills profile from encrypted settings."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()

    if not row or not row[0]:
        return DEFAULT_PROFILE

    settings = json.loads(row[0])
    profile_data = settings.get("survival", {}).get("profile", {})
    return _profile_from_data(profile_data)


async def update_profile(
    config: Config, user_id: str, updates: dict[str, object]
) -> SkillsProfile:
    """Update skills profile atomically. Returns new immutable profile."""
    # Read, merge, and write in a single db_session to avoid race conditions
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        settings = json.loads(row[0]) if row and row[0] else {}

        # Load current profile from settings
        profile_data = settings.get("survival", {}).get("profile", {})
        current = _profile_from_data(profile_data)

        # Merge current values with updates (convert tuples to lists for JSON)
        merged: dict[str, object] = {}
        for k, v in dataclasses.asdict(current).items():
            merged[k] = list(v) if isinstance(v, tuple) else v
        for k, v in updates.items():
            if k in _PROFILE_FIELDS:
                merged[k] = list(v) if isinstance(v, tuple) else v

        settings.setdefault("survival", {})["profile"] = merged
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(settings), user_id),
        )
        await db.commit()

    return await get_profile(config, user_id)


def _profile_from_data(data: dict) -> SkillsProfile:
    """Build SkillsProfile from raw dict (e.g. from JSON settings)."""
    if not data:
        return DEFAULT_PROFILE
    cleaned: dict[str, object] = {}
    for k, v in data.items():
        if k not in _PROFILE_FIELDS:
            continue
        if k in _TUPLE_FIELDS and isinstance(v, list):
            cleaned[k] = tuple(v)
        else:
            cleaned[k] = v
    return SkillsProfile(**cleaned)
