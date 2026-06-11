"""Permission checker — resolves the effective permission level for a skill."""

from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.permissions.models import (
    ALLOW,
    ASK,
    DEFAULT_CATEGORY_PERMISSIONS,
    SENSITIVE_SKILL_DEFAULTS,
    ResolvedPermission,
)
from lazyclaw.permissions.settings import get_permission_settings
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.skills.tool_namespace import bare_tool_name

logger = logging.getLogger(__name__)


class PermissionChecker:
    """Resolves per-skill permission levels from user settings + category defaults."""

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        self._config = config
        self._registry = registry

    async def check(self, user_id: str, skill_name: str) -> ResolvedPermission:
        """Resolve the permission level for a specific skill.

        Resolution order:
        1. Skill-level override (highest priority; matches the bare MCP
           name too, so ``/allow upwork_accept_offer`` reaches the
           dynamic ``mcp_<uuid>_upwork_accept_offer`` id)
        2. Sensitive skill-level default (money movers — beats every
           category default, including a user's blanket one)
        3. Category default from user settings
        4. Global category default from models.py
        5. Fallback: 'ask'
        """
        settings = await get_permission_settings(self._config, user_id)
        bare_name = bare_tool_name(skill_name)

        # 1. Check skill-level override (exact id first, then bare name)
        overrides = settings.get("skill_overrides", {})
        for key in (skill_name, bare_name):
            if key in overrides:
                return ResolvedPermission(
                    skill_name=skill_name,
                    level=overrides[key],
                    source="skill_override",
                )

        # 2. Sensitive skill-level defaults — only an explicit per-skill
        # override above may re-open these.
        if bare_name in SENSITIVE_SKILL_DEFAULTS:
            return ResolvedPermission(
                skill_name=skill_name,
                level=SENSITIVE_SKILL_DEFAULTS[bare_name],
                source="sensitive_default",
            )

        # 3. Determine category
        skill = self._registry.get(skill_name)
        category = skill.category if skill else "unknown"

        # 4. Check user's category defaults
        cat_defaults = settings.get("category_defaults", {})
        if category in cat_defaults:
            return ResolvedPermission(
                skill_name=skill_name,
                level=cat_defaults[category],
                source="category_default",
            )

        # 5. Check global category defaults
        if category in DEFAULT_CATEGORY_PERMISSIONS:
            return ResolvedPermission(
                skill_name=skill_name,
                level=DEFAULT_CATEGORY_PERMISSIONS[category],
                source="category_default",
            )

        # 6. Fallback
        return ResolvedPermission(
            skill_name=skill_name,
            level=ASK,
            source="category_default",
        )

    async def check_effective(
        self, user_id: str, skill_name: str
    ) -> ResolvedPermission:
        """Resolve permission, then layer the operating-mode posture on top.

        ``check()`` returns the BASE decision (used by the permissions UI);
        execution goes through here so the Chat/Ask/Plan/Auto posture
        (ADR-0005 Phase 3) takes effect. ASK mode (the default) is a no-op.
        """
        base = await self.check(user_id, skill_name)
        try:
            from lazyclaw.runtime.agent_mode import (
                apply_mode_posture,
                get_agent_mode,
                is_readonly_skill,
            )

            mode = await get_agent_mode(self._config, user_id)
            skill = self._registry.get(skill_name)
            category = skill.category if skill else None
            return apply_mode_posture(
                mode, base, is_readonly=is_readonly_skill(skill_name, category)
            )
        except Exception:
            # Never let mode resolution break tool execution — fall back to base.
            logger.debug("mode posture failed; using base permission", exc_info=True)
            return base

    async def is_allowed(self, user_id: str, skill_name: str) -> bool:
        """Quick check: is this skill allowed without approval (mode-aware)?"""
        resolved = await self.check_effective(user_id, skill_name)
        return resolved.level == ALLOW

    async def resolve_all(self, user_id: str) -> list[ResolvedPermission]:
        """Resolve permissions for all registered skills."""
        results: list[ResolvedPermission] = []
        for names in self._registry.list_by_category().values():
            for name in names:
                resolved = await self.check(user_id, name)
                results.append(resolved)
        return results
