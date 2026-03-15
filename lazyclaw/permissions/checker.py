"""Permission checker — resolves the effective permission level for a skill."""

from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.permissions.models import (
    ALLOW,
    ASK,
    DEFAULT_CATEGORY_PERMISSIONS,
    ResolvedPermission,
)
from lazyclaw.permissions.settings import get_permission_settings
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class PermissionChecker:
    """Resolves per-skill permission levels from user settings + category defaults."""

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        self._config = config
        self._registry = registry

    async def check(self, user_id: str, skill_name: str) -> ResolvedPermission:
        """Resolve the permission level for a specific skill.

        Resolution order:
        1. Skill-level override (highest priority)
        2. Category default from user settings
        3. Global category default from models.py
        4. Fallback: 'ask'
        """
        settings = await get_permission_settings(self._config, user_id)

        # 1. Check skill-level override
        overrides = settings.get("skill_overrides", {})
        if skill_name in overrides:
            return ResolvedPermission(
                skill_name=skill_name,
                level=overrides[skill_name],
                source="skill_override",
            )

        # 2. Determine category
        skill = self._registry.get(skill_name)
        category = skill.category if skill else "unknown"

        # 3. Check user's category defaults
        cat_defaults = settings.get("category_defaults", {})
        if category in cat_defaults:
            return ResolvedPermission(
                skill_name=skill_name,
                level=cat_defaults[category],
                source="category_default",
            )

        # 4. Check global category defaults
        if category in DEFAULT_CATEGORY_PERMISSIONS:
            return ResolvedPermission(
                skill_name=skill_name,
                level=DEFAULT_CATEGORY_PERMISSIONS[category],
                source="category_default",
            )

        # 5. Fallback
        return ResolvedPermission(
            skill_name=skill_name,
            level=ASK,
            source="category_default",
        )

    async def is_allowed(self, user_id: str, skill_name: str) -> bool:
        """Quick check: is this skill allowed without approval?"""
        resolved = await self.check(user_id, skill_name)
        return resolved.level == ALLOW

    async def resolve_all(self, user_id: str) -> list[ResolvedPermission]:
        """Resolve permissions for all registered skills."""
        results: list[ResolvedPermission] = []
        for names in self._registry.list_by_category().values():
            for name in names:
                resolved = await self.check(user_id, name)
                results.append(resolved)
        return results
