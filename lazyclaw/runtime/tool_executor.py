from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.permissions.models import ALLOW, DENY
from lazyclaw.runtime.tool_result import ToolResult
from lazyclaw.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from lazyclaw.runtime.callbacks import AgentCallback

logger = logging.getLogger(__name__)

# Prefix returned when a tool call requires user approval
APPROVAL_PREFIX = "APPROVAL_REQUIRED:"

# Default timeout for tool execution (seconds)
DEFAULT_TOOL_TIMEOUT = 60


class ToolExecutor:
    def __init__(
        self, registry: SkillRegistry, permission_checker=None, timeout: int = DEFAULT_TOOL_TIMEOUT,
    ) -> None:
        self._registry = registry
        self._checker = permission_checker
        self._timeout = timeout

    async def execute(
        self,
        tool_call: ToolCall,
        user_id: str,
        callback: AgentCallback | None = None,
    ) -> str:
        """Execute a tool call, checking permissions first.

        Returns APPROVAL_REQUIRED:skill_name:{args_json} if permission level is 'ask'.
        Returns an error string if permission level is 'deny'.

        If the skill returns a ``ToolResult`` with attachments, fires
        ``attachment`` events via *callback* so channels can deliver them.
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
            result = await asyncio.wait_for(
                skill.execute(user_id, tool_call.arguments),
                timeout=self._timeout,
            )
            logger.debug("Tool %s executed successfully", tool_call.name)
            return await self._process_result(result, tool_call.name, callback)
        except asyncio.TimeoutError:
            logger.error("Tool %s timed out after %ds", tool_call.name, self._timeout)
            return f"Error: Tool '{tool_call.name}' timed out after {self._timeout} seconds."
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_call.name, e)
            return f"Error executing {tool_call.name}: {e}"

    async def execute_allowed(
        self,
        tool_call: ToolCall,
        user_id: str,
        callback: AgentCallback | None = None,
    ) -> str:
        """Execute a tool call WITHOUT permission checks.

        Only call this after the user has explicitly approved the action.
        """
        skill = self._registry.get(tool_call.name)
        if not skill:
            return f"Error: Unknown tool '{tool_call.name}'"

        try:
            result = await asyncio.wait_for(
                skill.execute(user_id, tool_call.arguments),
                timeout=self._timeout,
            )
            logger.debug("Tool %s executed (approved)", tool_call.name)
            return await self._process_result(result, tool_call.name, callback)
        except asyncio.TimeoutError:
            logger.error("Tool %s timed out after %ds", tool_call.name, self._timeout)
            return f"Error: Tool '{tool_call.name}' timed out after {self._timeout} seconds."
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_call.name, e)
            return f"Error executing {tool_call.name}: {e}"

    async def _process_result(
        self,
        result: str | ToolResult,
        tool_name: str,
        callback: AgentCallback | None,
    ) -> str:
        """Extract text from result and fire attachment events if present."""
        if not isinstance(result, ToolResult):
            return str(result)

        # Fire attachment events for channels to deliver
        if callback and result.attachments:
            from lazyclaw.runtime.callbacks import AgentEvent

            for att in result.attachments:
                await callback.on_event(AgentEvent(
                    kind="attachment",
                    detail=att.filename or tool_name,
                    metadata={
                        "data": att.data,
                        "media_type": att.media_type,
                        "filename": att.filename,
                    },
                ))

        return result.text
