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
            "Platforms available: upwork, indeed, glassdoor, freelancer, fiverr. "
            "Usage: 'my skills are python, fastapi, react' or "
            "'set minimum rate $40/hour' or 'set title Senior Python Developer'"
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
                    "description": "Your professional skills",
                },
                "title": {
                    "type": "string",
                    "description": "Professional title",
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
                    "description": "Minimum fixed price in USD",
                },
                "platforms": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["upwork", "indeed", "glassdoor", "freelancer", "fiverr"],
                    },
                    "description": "Platforms to hunt on: upwork, indeed, glassdoor, freelancer, fiverr",
                },
                "excluded_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to exclude from job results",
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
            if not profile.skills:
                return "No skills profile set. Tell me your skills, title, and rates."
            branding = "LazyClaw AI Agent" if profile.branding_mode == "lazyclaw" else "Personal"
            return (
                f"Your Skills Profile:\n\n"
                f"Identity: {branding}\n"
                f"Title: {profile.title or 'Not set'}\n"
                f"Skills: {', '.join(profile.skills) or 'None'}\n"
                f"Bio: {profile.bio or 'Not set'}\n"
                f"Min hourly: ${profile.min_hourly_rate}/hr\n"
                f"Min fixed: ${profile.min_fixed_rate}\n"
                f"Platforms: {', '.join(profile.platforms)}\n"
                f"Excluded: {', '.join(profile.excluded_keywords) or 'None'}\n"
                f"Max concurrent: {profile.max_concurrent_jobs}"
            )

        # Validate and coerce values before saving
        updates = _coerce_updates(raw_updates)
        if isinstance(updates, str):
            return updates  # validation error message

        await update_profile(self._config, user_id, updates)
        changed = ", ".join(f"{k}={v}" for k, v in updates.items())
        return f"Profile updated: {changed}"
