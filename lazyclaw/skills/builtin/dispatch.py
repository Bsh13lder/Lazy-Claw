"""DispatchSubagentsSkill — lets the main agent spawn parallel subagents.

The LLM calls this with a list of tasks and agent types. Each subagent runs
in an isolated context (no parent conversation history) with type-appropriate
tools. Results are merged into a structured summary returned to the main agent.

Single-depth enforced: subagents cannot call this tool (context var + tool
exclusion both prevent it).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lazyclaw.skills.base import BaseSkill
from lazyclaw.runtime.dispatcher import (
    AgentDispatcher,
    AgentType,
    SubagentConfig,
    SubagentResult,
    _IS_SUBAGENT,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Max chars of a single subagent result included in the merged summary.
# Prevents context blow-up when subagents return large payloads.
_MAX_RESULT_CHARS = 800


class DispatchSubagentsSkill(BaseSkill):
    """Dispatch 2+ independent subtasks to parallel subagents.

    Use when:
    - 3+ independent tasks can run concurrently (research, fetch, analyse)
    - Research/search subtasks → 'explore' type (cheap, read-only)
    - State-mutation subtasks → 'general_purpose' type (full access)
    - Scoped tool subtasks → 'specialist' type with explicit tool_names

    Each subagent runs with isolated context — no conversation history.
    Results are returned as a structured summary.

    Cannot be called from within a subagent (single-depth limit).
    """

    def __init__(
        self,
        config: Config,
        registry: SkillRegistry,
        eco_router: EcoRouter,
        permission_checker=None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker

    # Parallel subagents can each take up to 60s → allow 5 min total
    timeout = 300

    @property
    def name(self) -> str:
        return "dispatch_subagents"

    @property
    def display_name(self) -> str:
        return "Dispatch Subagents"

    @property
    def description(self) -> str:
        return (
            "Dispatch 2+ independent subtasks to parallel subagents and collect "
            "results. Use for 3+ concurrent research or execution tasks. "
            "Types: 'explore' (read-only, fast), 'general_purpose' (full access), "
            "'specialist' (scoped tools). Each subagent has isolated context."
        )

    @property
    def category(self) -> str:
        return "orchestration"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "minItems": 2,
                    "description": "Independent tasks to run in parallel (minimum 2)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["explore", "general_purpose", "specialist"],
                                "description": (
                                    "explore: read-only research (search, read files, browser). "
                                    "general_purpose: full tool access for complex tasks. "
                                    "specialist: provide tool_names for scoped execution."
                                ),
                            },
                            "task": {
                                "type": "string",
                                "description": (
                                    "Clear, self-contained instruction for this subagent. "
                                    "Include all context it needs — it has no conversation history."
                                ),
                            },
                            "tool_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Required for 'specialist'. Optional override for other types."
                                ),
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "Max seconds for this subagent (default: 60)",
                            },
                        },
                        "required": ["type", "task"],
                    },
                },
            },
            "required": ["tasks"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        # Single-depth enforcement — subagents cannot spawn subagents
        if _IS_SUBAGENT.get():
            return (
                "Error: subagents cannot dispatch further subagents. "
                "Single-depth limit enforced."
            )

        raw_tasks: list[dict] = params.get("tasks", [])
        if not raw_tasks:
            return "Error: tasks list is empty"
        if len(raw_tasks) < 2:
            return (
                "Error: dispatch_subagents requires at least 2 tasks. "
                "Use the delegate tool for single-specialist tasks."
            )

        configs: list[SubagentConfig] = []
        for raw in raw_tasks:
            type_str = raw.get("type", "")
            try:
                agent_type = AgentType(type_str)
            except ValueError:
                return (
                    f"Error: invalid agent type '{type_str}'. "
                    f"Use: explore, general_purpose, specialist"
                )

            task_str = (raw.get("task") or "").strip()
            if not task_str:
                return "Error: each task must have a non-empty 'task' field"

            tool_names: tuple[str, ...] | None = None
            if "tool_names" in raw and raw["tool_names"]:
                tool_names = tuple(raw["tool_names"])

            if agent_type == AgentType.SPECIALIST and not tool_names:
                return (
                    "Error: 'specialist' type requires 'tool_names' to be specified"
                )

            configs.append(SubagentConfig(
                agent_type=agent_type,
                task=task_str,
                tool_names=tool_names,
                timeout=int(raw.get("timeout", 60)),
            ))

        dispatcher = AgentDispatcher(
            config=self._config,
            eco_router=self._eco_router,
            registry=self._registry,
            permission_checker=self._permission_checker,
        )

        logger.info(
            "dispatch_subagents: spawning %d subagents in parallel — %s",
            len(configs),
            [(c.agent_type.value, c.task[:40]) for c in configs],
        )

        results = await dispatcher.dispatch(configs, user_id)
        return _format_results(results)


def _format_results(results: list[SubagentResult]) -> str:
    """Format SubagentResult list into a structured LLM-readable summary."""
    succeeded = sum(1 for r in results if r.success)
    lines = [
        f"[Subagent Dispatch: {len(results)} tasks, "
        f"{succeeded} succeeded, {len(results) - succeeded} failed]\n"
    ]
    for i, r in enumerate(results, 1):
        status = "OK" if r.success else "FAIL"
        lines.append(
            f"--- Task {i} [{r.agent_type.value}] {status} ({r.duration_ms}ms) ---"
        )
        lines.append(f"Task: {r.task[:100]}")
        if r.success and r.result:
            preview = r.result[:_MAX_RESULT_CHARS]
            if len(r.result) > _MAX_RESULT_CHARS:
                preview += f"\n[...{len(r.result) - _MAX_RESULT_CHARS} chars truncated]"
            lines.append(f"Result:\n{preview}")
        elif not r.success:
            lines.append(f"Error: {r.error}")
        else:
            lines.append("Result: (empty)")
        lines.append("")
    return "\n".join(lines)
