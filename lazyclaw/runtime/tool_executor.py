from __future__ import annotations

import json
import logging

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.permissions.models import ALLOW, DENY
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Prefix returned when a tool call requires user approval
APPROVAL_PREFIX = "APPROVAL_REQUIRED:"


class ToolExecutor:
    def __init__(self, registry: SkillRegistry, permission_checker=None) -> None:
        self._registry = registry
        self._checker = permission_checker

    async def execute(self, tool_call: ToolCall, user_id: str) -> str:
        """Execute a tool call, checking permissions first.

        Returns APPROVAL_REQUIRED:skill_name:{args_json} if permission level is 'ask'.
        Returns an error string if permission level is 'deny'.
        """
        skill = self._registry.get(tool_call.name)
        if not skill:
            return f"Error: Unknown tool '{tool_call.name}'"

        # Permission check (if checker is configured)
        if self._checker is not None:
            resolved = await self._checker.check(user_id, tool_call.name)
            if resolved.level == DENY:
                logger.info("Tool %s denied for user %s", tool_call.name, user_id)
                return f"Error: Tool '{tool_call.name}' is not permitted. The user has denied this action."
            if resolved.level != ALLOW:
                # Requires approval — return marker for the agent loop
                args_json = json.dumps(tool_call.arguments) if tool_call.arguments else "{}"
                logger.info("Tool %s requires approval for user %s", tool_call.name, user_id)
                return f"{APPROVAL_PREFIX}{tool_call.name}:{args_json}"

        try:
            result = await skill.execute(user_id, tool_call.arguments)
            logger.debug("Tool %s executed successfully", tool_call.name)
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_call.name, e)
            return f"Error executing {tool_call.name}: {e}"
