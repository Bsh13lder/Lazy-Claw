"""Set or view the user's freelance skills profile."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SetSkillsProfileSkill(BaseSkill):
    """Set or view the user's freelance skills profile."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_skills_profile"

    @property
    def description(self) -> str:
        return (
            "Set your freelance skills profile for job matching. "
            "Platforms: upwork, indeed, glassdoor, freelancer, fiverr. "
            "Examples: 'my skills are python, fastapi, react' / "
            "'set min rate $40/hour' / 'set title Senior Python Developer' / "
            "'set tiny gig cap to 75' / 'only search indeed and linkedin' / "
            "'show only jobs from last 24 hours' / "
            "'switch branding to personal' / "
            "'show my skills profile' (no args = view)."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Your professional skills (e.g. python, react, figma)",
                },
                "title": {
                    "type": "string",
                    "description": "Professional title (e.g. 'Senior Python Developer')",
                },
                "bio": {
                    "type": "string",
                    "description": "Short professional bio (2-3 sentences)",
                },
                "min_hourly_rate": {
                    "type": "number",
                    "description": "Minimum hourly rate in USD",
                },
                "min_fixed_rate": {
                    "type": "number",
                    "description": "Minimum fixed-price job amount in USD",
                },
                "platforms": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["upwork", "indeed", "glassdoor", "freelancer", "fiverr"],
                    },
                    "description": "Platforms to hunt on",
                },
                "excluded_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to filter OUT of results",
                },
                "max_tiny_gig_budget": {
                    "type": "number",
                    "description": "Tiny-gig matcher bonus cap in USD (default 100). Jobs at or below this budget get a +0.10 score boost. NL: 'set tiny gig cap to 75'.",
                },
                "default_search_sites": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["indeed", "linkedin", "glassdoor", "zip_recruiter", "google"],
                    },
                    "description": "JobSpy sites to search by default. NL: 'only search indeed and linkedin'.",
                },
                "default_results_per_search": {
                    "type": "integer",
                    "description": "Max results per platform per search (default 25, max 50).",
                },
                "default_hours_old": {
                    "type": "integer",
                    "description": "Only show jobs posted within this many hours (default 72). NL: 'only show jobs from last 24 hours'.",
                },
                "branding_mode": {
                    "type": "string",
                    "enum": ["lazyclaw", "personal"],
                    "description": "lazyclaw = transparent AI agent identity; personal = human freelancer. NL: 'switch branding to personal'.",
                },
                "preferred_categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Categories that get a small matcher bonus (e.g. 'web scraping', 'data extraction').",
                },
                "work_hours": {
                    "type": "string",
                    "description": "Free-text work hours, e.g. 'flexible', 'EU business hours'.",
                },
                "max_concurrent_jobs": {
                    "type": "integer",
                    "description": "Max active gigs at once (default 2).",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.profile import _coerce_updates, get_profile, update_profile

        raw_updates = {
            k: v
            for k, v in params.items()
            if v is not None and v != "" and v != []
        }

        if not raw_updates:
            profile = await get_profile(self._config, user_id)
            branding = "LazyClaw AI Agent" if profile.branding_mode == "lazyclaw" else "Personal"
            return (
                f"Your Skills Profile:\n\n"
                f"Identity: {branding}\n"
                f"Title: {profile.title or 'Not set'}\n"
                f"Skills: {', '.join(profile.skills) or 'None'}\n"
                f"Bio: {profile.bio or 'Not set'}\n"
                f"Min hourly: ${profile.min_hourly_rate}/hr\n"
                f"Min fixed: ${profile.min_fixed_rate}\n"
                f"Tiny-gig cap: ${profile.max_tiny_gig_budget}\n"
                f"Platforms: {', '.join(profile.platforms) or 'None'}\n"
                f"Default sites: {', '.join(profile.default_search_sites) or 'indeed'}\n"
                f"Default hours: last {profile.default_hours_old}h, "
                f"{profile.default_results_per_search} results/site\n"
                f"Categories: {', '.join(profile.preferred_categories) or 'None'}\n"
                f"Excluded: {', '.join(profile.excluded_keywords) or 'None'}\n"
                f"Work hours: {profile.work_hours}\n"
                f"Max concurrent: {profile.max_concurrent_jobs}"
            )

        # Validate and coerce values before saving
        updates = _coerce_updates(raw_updates)
        if isinstance(updates, str):
            return updates  # validation error message

        await update_profile(self._config, user_id, updates)
        changed = ", ".join(f"{k}={v}" for k, v in updates.items())
        return f"Profile updated: {changed}"
