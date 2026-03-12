from __future__ import annotations

import logging

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_call: ToolCall, user_id: str) -> str:
        skill = self._registry.get(tool_call.name)
        if not skill:
            return f"Error: Unknown tool '{tool_call.name}'"
        try:
            result = await skill.execute(user_id, tool_call.arguments)
            logger.debug("Tool %s executed successfully", tool_call.name)
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_call.name, e)
            return f"Error executing {tool_call.name}: {e}"
