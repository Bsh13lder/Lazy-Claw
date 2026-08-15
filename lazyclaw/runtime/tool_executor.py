from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.permissions.audit import log_action
from lazyclaw.permissions.models import ALLOW, DENY
from lazyclaw.runtime.skill_lesson_auto import (
    outcome_from_result,
    record_skill_outcome,
)
from lazyclaw.runtime.browser_turn_lock import acquire_live_browser_if_needed
from lazyclaw.runtime.tool_result import ToolResult
from lazyclaw.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.runtime.callbacks import AgentCallback

logger = logging.getLogger(__name__)

# Prefix returned when a tool call requires user approval
APPROVAL_PREFIX = "APPROVAL_REQUIRED:"

# Default timeout for tool execution (seconds)
DEFAULT_TOOL_TIMEOUT = 60

# ── Per-tool timeout overrides (2026-08-15 timeout-hierarchy audit) ──────
# The flat 60s default is right for a memory lookup and badly wrong for a
# browser action: ONE Cloudflare-challenged navigation through the host
# Brave routinely exceeds 60s, so the action died mid-navigation
# ("[toolexec] Tool browser timed out after 60s") and the brain retried in
# background — three generations, zero results (2026-08-14 18:31-18:40).
#
# NESTING RULE — a child budget must always be strictly smaller than its
# parent's, or the parent expires first, orphans the child and reports a
# timeout the child never saw. This cap (180s) is the innermost budget in
# the dispatch chain and sits well under the sync browser specialist floor
# (_BROWSER_SYNC_TIMEOUT_FLOOR_S = 480s in skills/builtin/agent_tool.py),
# which itself sits under the sync/background ceiling of 600s.
# tests/runtime/test_timeout_hierarchy.py pins the whole chain.
#
# Keep this table SMALL: it is an escape hatch for tools whose real-world
# tail latency does not fit the default, not a per-skill config surface.
PER_TOOL_TIMEOUTS: dict[str, int] = {
    "browser": 180,
}


def resolve_tool_timeout(skill: object, name: str, default: int) -> int:
    """Effective per-call timeout for *skill*, most specific source wins.

    1. ``skill.timeout`` — an explicit declaration on the skill class
       (e.g. ``AgentDispatchSkill.timeout``, which must exceed its own
       inner ``wait_for`` budget so the executor never kills a dispatch
       that is still inside its declared budget).
    2. ``PER_TOOL_TIMEOUTS[name]`` — the runtime override table above.
    3. *default* — the executor default (``DEFAULT_TOOL_TIMEOUT``).
    """
    declared = getattr(skill, "timeout", None)
    if declared:
        return int(declared)
    return PER_TOOL_TIMEOUTS.get(name, default)


class ToolExecutor:
    def __init__(
        self,
        registry: SkillRegistry,
        permission_checker=None,
        timeout: int = DEFAULT_TOOL_TIMEOUT,
        config: "Config | None" = None,
    ) -> None:
        self._registry = registry
        self._checker = permission_checker
        self._timeout = timeout
        # Optional. Without it the auto-recorder no-ops (record_skill_outcome
        # gates on config presence). Wired by agent.py at construction time.
        self._config = config

    async def _audit(self, user_id: str, action: str, tool_call: ToolCall) -> None:
        """Best-effort audit write (2026-06-10 audit, Phase 3).

        ``log_action`` is itself fire-and-forget, but the wiring is also
        wrapped so a bug here can never break tool execution.
        """
        if self._config is None:
            return
        try:
            await log_action(
                self._config, user_id, action,
                skill_name=tool_call.name,
                arguments=tool_call.arguments or None,
            )
        except Exception:
            logger.debug("audit write swallowed for %s", tool_call.name, exc_info=True)

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

        # Permission check (if checker is configured). Use the mode-aware
        # resolver when available (ADR-0005 Phase 3) so Chat/Ask/Plan/Auto
        # posture takes effect; fall back to plain check() for any
        # checker-like object that predates it.
        if self._checker is not None:
            check_fn = getattr(self._checker, "check_effective", self._checker.check)
            resolved = await check_fn(user_id, tool_call.name)
            if resolved.level == DENY:
                logger.info("Tool %s denied for user %s", tool_call.name, user_id)
                await self._audit(user_id, "tool_denied", tool_call)
                return f"Error: Tool '{tool_call.name}' is not permitted. The user has denied this action."
            if resolved.level != ALLOW:
                # Requires approval — return marker for the agent loop
                args_json = json.dumps(tool_call.arguments) if tool_call.arguments else "{}"
                logger.info("Tool %s requires approval for user %s", tool_call.name, user_id)
                return f"{APPROVAL_PREFIX}{tool_call.name}:{args_json}"

        # Serialize live-Brave access: if this tool drives the user's single
        # live host Brave, hold the per-user lock for the rest of the turn so a
        # concurrent foreground / background / watcher turn can't steal the tab
        # mid-sequence (the 2026-05-29 research-vs-submit collision).
        await acquire_live_browser_if_needed(user_id, tool_call.name)

        logger.debug(
            "[toolexec] execute start name=%s arg_keys=%s user=%s",
            tool_call.name,
            list(tool_call.arguments.keys()) if tool_call.arguments else [],
            user_id[:8] if user_id else user_id,
        )
        try:
            # Per-call timeout: skill.timeout > PER_TOOL_TIMEOUTS > default
            effective_timeout = resolve_tool_timeout(
                skill, tool_call.name, self._timeout,
            )
            result = await asyncio.wait_for(
                skill.execute(user_id, tool_call.arguments),
                timeout=effective_timeout,
            )
            logger.debug("Tool %s executed successfully", tool_call.name)
            processed = await self._process_result(result, tool_call.name, callback)
            logger.debug(
                "[toolexec] execute done name=%s result_len=%d",
                tool_call.name, len(processed) if processed else 0,
            )
            # Surface failed-tool results at INFO so we can debug MCP errors
            # without having to decrypt the lesson store. The classifier
            # already runs inside record_skill_outcome — calling it here a
            # second time is cheap (pure string scan) and lets us log the
            # actual payload that the brain saw before it gave up.
            try:
                _outcome, _err, _snippet = outcome_from_result(processed, None)
                if _outcome == "failed":
                    text = _snippet if _snippet is not None else str(processed)
                    logger.info(
                        "Tool %s FAILED result (first 800 chars): %s",
                        tool_call.name, text[:800],
                    )
            except Exception:
                logger.debug("tool-result failure logging swallowed", exc_info=True)
            await record_skill_outcome(
                self._config, user_id, skill, tool_call.arguments, processed,
            )
            await self._audit(user_id, "tool_executed", tool_call)
            return processed
        except asyncio.TimeoutError as exc:
            effective_timeout = resolve_tool_timeout(
                skill, tool_call.name, self._timeout,
            )
            logger.error(
                "[toolexec] Tool %s timed out after %ds (user=%s)",
                tool_call.name, effective_timeout, user_id[:8] if user_id else user_id,
            )
            await record_skill_outcome(
                self._config, user_id, skill, tool_call.arguments, None, exc,
            )
            return f"Error: Tool '{tool_call.name}' timed out after {effective_timeout} seconds."
        except Exception as e:
            logger.error(
                "[toolexec] Tool %s failed (user=%s) type=%s: %s",
                tool_call.name, user_id[:8] if user_id else user_id, type(e).__name__, e,
                exc_info=True,
            )
            await record_skill_outcome(
                self._config, user_id, skill, tool_call.arguments, None, e,
            )
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

        await acquire_live_browser_if_needed(user_id, tool_call.name)

        logger.debug(
            "[toolexec] execute_allowed start name=%s arg_keys=%s user=%s",
            tool_call.name,
            list(tool_call.arguments.keys()) if tool_call.arguments else [],
            user_id[:8] if user_id else user_id,
        )
        try:
            effective_timeout = resolve_tool_timeout(
                skill, tool_call.name, self._timeout,
            )
            result = await asyncio.wait_for(
                skill.execute(user_id, tool_call.arguments),
                timeout=effective_timeout,
            )
            logger.debug("Tool %s executed (approved)", tool_call.name)
            processed = await self._process_result(result, tool_call.name, callback)
            logger.debug(
                "[toolexec] execute_allowed done name=%s result_len=%d",
                tool_call.name, len(processed) if processed else 0,
            )
            await record_skill_outcome(
                self._config, user_id, skill, tool_call.arguments, processed,
            )
            await self._audit(user_id, "tool_approved", tool_call)
            return processed
        except asyncio.TimeoutError as exc:
            effective_timeout = resolve_tool_timeout(
                skill, tool_call.name, self._timeout,
            )
            logger.error(
                "[toolexec] Tool %s (approved) timed out after %ds (user=%s)",
                tool_call.name, effective_timeout, user_id[:8] if user_id else user_id,
            )
            await record_skill_outcome(
                self._config, user_id, skill, tool_call.arguments, None, exc,
            )
            return f"Error: Tool '{tool_call.name}' timed out after {effective_timeout} seconds."
        except Exception as e:
            logger.error(
                "[toolexec] Tool %s (approved) failed type=%s: %s",
                tool_call.name, type(e).__name__, e, exc_info=True,
            )
            await record_skill_outcome(
                self._config, user_id, skill, tool_call.arguments, None, e,
            )
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

        logger.debug(
            "[toolexec] batch total=%d read_only=%d state=%d",
            len(tool_calls), len(read_only_indices), len(state_indices),
        )

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

            logger.debug(
                "[toolexec] firing %d attachment event(s) for %s",
                len(result.attachments), tool_name,
            )
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
