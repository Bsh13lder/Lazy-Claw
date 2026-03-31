from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
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
            # Per-tool timeout: skill.timeout overrides executor default
            effective_timeout = getattr(skill, "timeout", None) or self._timeout
            result = await asyncio.wait_for(
                skill.execute(user_id, tool_call.arguments),
                timeout=effective_timeout,
            )
            logger.debug("Tool %s executed successfully", tool_call.name)
            return await self._process_result(result, tool_call.name, callback)
        except asyncio.TimeoutError:
            effective_timeout = getattr(skill, "timeout", None) or self._timeout
            logger.error("Tool %s timed out after %ds", tool_call.name, effective_timeout)
            return f"Error: Tool '{tool_call.name}' timed out after {effective_timeout} seconds."
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
            effective_timeout = getattr(skill, "timeout", None) or self._timeout
            result = await asyncio.wait_for(
                skill.execute(user_id, tool_call.arguments),
                timeout=effective_timeout,
            )
            logger.debug("Tool %s executed (approved)", tool_call.name)
            return await self._process_result(result, tool_call.name, callback)
        except asyncio.TimeoutError:
            effective_timeout = getattr(skill, "timeout", None) or self._timeout
            logger.error("Tool %s timed out after %ds", tool_call.name, effective_timeout)
            return f"Error: Tool '{tool_call.name}' timed out after {effective_timeout} seconds."
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_call.name, e)
            return f"Error executing {tool_call.name}: {e}"

    async def execute_batch(
        self,
        tool_calls: list[ToolCall],
        user_id: str,
        callback: AgentCallback | None = None,
    ) -> list[tuple[ToolCall, str, int, str | None]]:
        """Execute multiple tool calls with parallelism for read-only tools.

        Read-only tools (skill.read_only == True) run concurrently via
        asyncio.gather.  State-modifying tools run sequentially after the
        read-only batch completes.

        Returns a list of (tool_call, result, duration_ms, parallel_group_id)
        in the **same order** as the input list.  parallel_group_id is a short
        hex string shared by all tools that ran concurrently; None for tools
        that ran sequentially.
        """
        if not tool_calls:
            return []

        # Separate read-only tools from state-modifying tools, preserving order.
        read_only_indices: list[int] = []
        state_indices: list[int] = []
        for i, tc in enumerate(tool_calls):
            skill = self._registry.get(tc.name)
            if skill and getattr(skill, "read_only", False):
                read_only_indices.append(i)
            else:
                state_indices.append(i)

        results: list[tuple[ToolCall, str, int, str | None] | None] = [None] * len(tool_calls)

        # ── Read-only tools: run concurrently ──────────────────────────────
        if read_only_indices:
            group_id: str | None = None
            if len(read_only_indices) > 1:
                import hashlib
                group_id = hashlib.sha1(  # noqa: S324 — not for security
                    json.dumps([tool_calls[i].name for i in read_only_indices]).encode()
                ).hexdigest()[:8]

            async def _timed_exec(tc: ToolCall) -> tuple[ToolCall, str, int]:
                t0 = time.monotonic()
                result = await self.execute(tc, user_id, callback)
                duration_ms = int((time.monotonic() - t0) * 1000)
                return tc, result, duration_ms

            ro_calls = [tool_calls[i] for i in read_only_indices]
            ro_outcomes = await asyncio.gather(*[_timed_exec(tc) for tc in ro_calls])

            if len(read_only_indices) > 1:
                sequential_estimate_ms = sum(dur for _, _, dur in ro_outcomes)
                actual_ms = max(dur for _, _, dur in ro_outcomes)
                saved_ms = sequential_estimate_ms - actual_ms
                logger.info(
                    "Parallel tool execution: %d read-only tools in %dms "
                    "(sequential estimate: %dms, saved: %dms) [group=%s]",
                    len(read_only_indices), actual_ms, sequential_estimate_ms, saved_ms, group_id,
                )

            for list_idx, (tc, result, duration_ms) in zip(read_only_indices, ro_outcomes):
                results[list_idx] = (tc, result, duration_ms, group_id)

        # ── State-modifying tools: run sequentially ─────────────────────────
        for i in state_indices:
            tc = tool_calls[i]
            t0 = time.monotonic()
            result = await self.execute(tc, user_id, callback)
            duration_ms = int((time.monotonic() - t0) * 1000)
            results[i] = (tc, result, duration_ms, None)

        return results  # type: ignore[return-value]  # all slots filled above

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
