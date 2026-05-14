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
    # Default rate the bot bids AT when applying to hourly jobs (vs
    # ``min_hourly_rate`` which is the FLOOR for which jobs to consider).
    # Starting low improves visibility on a fresh profile; the user can
    # tune via NL: "set my default proposal rate to 25".
    default_proposal_rate: float = 15.0
    max_concurrent_jobs: int = 2
    platforms: tuple[str, ...] = ()
    preferred_categories: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    work_hours: str = "flexible"
    branding_mode: str = "lazyclaw"  # "lazyclaw" = AI agent identity, "personal" = human freelancer
    # Identity threaded into every proposal.
    # `display_name` is the SIGN-OFF on cover letters — must match the
    # name on the actual freelance platform profile, otherwise the client
    # sees a mismatch and trust drops. NL-set via `set_display_name`.
    display_name: str = ""
    # `value_pitch` is the polished "what I offer" paragraph the AI uses
    # as the warm opener of every proposal. Set via the dedicated NL skill
    # `set_freelance_pitch` which polishes the raw user input via the
    # ROLE_WORKER LLM before saving. Empty = proposal opener falls back to
    # generic skills-based phrasing.
    value_pitch: str = ""
    # Public portfolio link rendered in proposals. Empty = "About me"
    # block is shown without a clickable link.
    github_url: str = ""
    # Tiny-gig matcher bonus — `score_job` adds +0.10 when 0 < budget <= cap.
    # NL-editable via set_skills_profile: "set tiny gig cap to 75".
    max_tiny_gig_budget: float = 100.0
    # Default page size applied when SearchJobsSkill is invoked without
    # explicit args. NL-editable.
    default_results_per_search: int = 25
    # Upwork search surface preference. "best_matches" (default) uses
    # Upwork's personalized recs at /nx/find-work/best-matches — respects
    # the filters the user set on Upwork's side (fixed-price / hourly /
    # budget range / category). "search" forces global keyword search.
    # SearchJobsSkill auto-overrides this per-call when the user passes
    # explicit keywords. NL-set via set_skills_profile:
    #   "match my upwork profile only" → best_matches
    #   "set upwork search to keyword" → search
    upwork_search_source: str = "best_matches"


# Python-leaning starter profile so a fresh user can run "find me jobs"
# immediately. User overrides via set_skills_profile from chat:
#   "my skills are figma, logo, branding"
#   "set platforms to upwork only"
#   "set tiny gig cap to 50"
DEFAULT_PROFILE = SkillsProfile(
    skills=("python", "fastapi", "scraping", "automation", "scripting"),
    title="Python Developer",
    bio="AI-assisted developer for fast scripting, scraping, and automation gigs.",
    min_hourly_rate=20.0,
    min_fixed_rate=20.0,
    platforms=("upwork",),
    max_tiny_gig_budget=100.0,
    branding_mode="lazyclaw",
    display_name="",
    value_pitch="",
    github_url="",
)

_PROFILE_FIELDS = frozenset(SkillsProfile.__dataclass_fields__.keys())

# Explicit list — safer than annotation introspection
_TUPLE_FIELDS: frozenset[str] = frozenset({
    "skills", "platforms", "preferred_categories", "excluded_keywords",
})

_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "min_hourly_rate", "min_fixed_rate", "max_tiny_gig_budget",
    "default_proposal_rate",
})

_INTEGER_FIELDS: frozenset[str] = frozenset({
    "max_concurrent_jobs", "default_results_per_search",
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
        elif k in _INTEGER_FIELDS:
            try:
                result[k] = int(float(str(v).strip()))
            except (ValueError, TypeError):
                return f"Invalid value for {k}: must be an integer."
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
