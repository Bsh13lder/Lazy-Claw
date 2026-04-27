"""DispatchSubagentsSkill — fan-out to parallel background subagents.

The LLM calls this with a list of tasks and agent types. Each subagent runs
in an isolated context (no parent conversation history) with type-appropriate
tools, and reports results asynchronously via ``background_done`` events on
the task event bus. The skill returns immediately with task IDs.

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
    _IS_SUBAGENT,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.runtime.team_lead import TeamLead
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class DispatchSubagentsSkill(BaseSkill):
    """Dispatch 2+ independent subtasks to parallel subagents.

    Always non-blocking: spawns subagents in the background and returns
    task IDs immediately. Subagent results stream back as
    ``background_done`` events that the agent absorbs on later turns.
    The user keeps chatting while subagents run.

    Use when:
    - 3+ independent tasks can run concurrently (research, fetch, analyse)
    - Research/search subtasks → 'explore' type (cheap, read-only)
    - State-mutation subtasks → 'general_purpose' type (full access)
    - Scoped tool subtasks → 'specialist' type with explicit tool_names

    Each subagent runs with isolated context — no conversation history.

    Cannot be called from within a subagent (single-depth limit).
    """

    def __init__(
        self,
        config: Config,
        registry: SkillRegistry,
        eco_router: EcoRouter,
        permission_checker=None,
        callback: AgentCallback | None = None,
        team_lead: TeamLead | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._callback = callback
        self._team_lead = team_lead

    # The skill returns immediately after spawning background subagents,
    # so a tight outer timeout is fine — we never block waiting for fan-out
    # results here.
    timeout = 30

    @property
    def name(self) -> str:
        return "dispatch_subagents"

    @property
    def display_name(self) -> str:
        return "Dispatch Subagents"

    @property
    def description(self) -> str:
        return (
            "Fire N independent research / scrape / draft tasks IN THE "
            "BACKGROUND and return immediately with task IDs. The subagents "
            "appear in the user's Activity panel under lane='subagent' and "
            "stream their results back as `background_done` events that you "
            "(the brain) absorb on later turns. "
            "DO NOT WAIT for the results in this turn — your tool-result is "
            "just the dispatch confirmation. Reply to the user with a short "
            "status ('I started 5 subagents, results will appear as they "
            "land') so they know work is in flight; the conversation stays "
            "responsive. "
            "Use for parallel work where the user can wait asynchronously: "
            "researching 10 companies, scraping 8 sites, drafting 6 "
            "proposals. For tasks where you need the merged answer in this "
            "same turn (single quick lookup, dependent reasoning), use "
            "`delegate` or call tools directly instead. "
            "Types: 'explore' (read-only, fastest, use liberally), "
            "'general_purpose' (full access, heavier), "
            "'specialist' (scoped tools via tool_names)."
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
                    "description": "Independent tasks to run in parallel (min 2, no hard max — aggressive fan-out encouraged for independent work)",
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
                # Default raised from 60s → 120s. Cold-start Playwright
                # (~5–15s) plus a real entity extraction + LLM round-trip
                # easily blew through the old 60s ceiling, leaving every
                # explore subagent timing out before producing output.
                timeout=int(raw.get("timeout", 120)),
            ))

        dispatcher = AgentDispatcher(
            config=self._config,
            eco_router=self._eco_router,
            registry=self._registry,
            permission_checker=self._permission_checker,
            team_lead=self._team_lead,
            callback=self._callback,
        )

        logger.info(
            "dispatch_subagents: spawning %d background subagents — %s",
            len(configs),
            [(c.agent_type.value, c.task[:40]) for c in configs],
        )

        task_ids = await dispatcher.submit_async(configs, user_id)
        breakdown = ", ".join(
            f"{c.agent_type.value}: {c.task[:50]}" for c in configs
        )
        return (
            f"Dispatched {len(task_ids)} subagents in the background "
            f"(lane='subagent'). They appear in the Activity panel and "
            f"stream results back as `background_done` events on later "
            f"turns. Reply to the user with a short status — DO NOT wait "
            f"for results in this turn.\n"
            f"Task IDs: {', '.join(task_ids)}\n"
            f"Tasks: {breakdown}"
        )
