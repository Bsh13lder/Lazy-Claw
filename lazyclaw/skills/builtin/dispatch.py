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
import uuid
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
        task_runner=None,
        chat_session_id: str | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._callback = callback
        self._team_lead = team_lead
        # RC2 — when a TaskRunner with a wired lane queue is available, the
        # dispatch's subagent results consolidate into ONE brain reply
        # (reusing the run_background brain-fan-out machinery) instead of
        # stranding in pending_subagent_notes that only drain on the next
        # user turn. chat_session_id routes the consolidation turn back to
        # the originating chat. Both optional → legacy fire-and-forget when
        # unset.
        self._task_runner = task_runner
        self._chat_session_id = chat_session_id

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
            "LEGACY — prefer the `agent` tool. "
            "Fire 2–5 TRULY DIFFERENT background tasks and return immediately "
            "with task IDs. Subagents appear in the Activity panel under "
            "lane='subagent' and stream results back as `background_done` "
            "events you (the brain) absorb on later turns. "
            "DO NOT WAIT for results in this turn — reply with a short "
            "status ('Started 3 subagents, I'll fold results into my next "
            "reply'). The conversation stays responsive. "
            "\n\n"
            "WHEN TO USE — STRICT:\n"
            "- 2–5 tasks, each a DIFFERENT goal (research X, scrape Y, "
            "summarize Z) → dispatch_subagents.\n"
            "- 1 long-running task (1 site, 1 thing) → use `run_background` "
            "instead. One worker, brain stays free, Telegram push when done.\n"
            "- ≥6 SIMILAR lookups (find email for 20 salons, scrape 50 "
            "URLs) → ONE `run_background` that calls a batch scraper tool "
            "(`mcp_scraper_batch_search_google`, `mcp_scraper_batch_crawl`). "
            "Spawning 20 subagents is wasteful: each cold-starts its own "
            "context, the concurrency cap is 4 so 16 wait in line, and most "
            "time out. Hard cap is 5 — calls with more will be rejected.\n"
            "- Need the merged answer in THIS turn → `delegate(...)` for one "
            "specialist, or call tools directly.\n"
            "\n"
            "Types: 'explore' (read-only, cheap, default), "
            "'general_purpose' (full tool access, heavier), "
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
                    "maxItems": 5,
                    "description": (
                        "Independent tasks to run in parallel (min 2, max 5). "
                        "For >5 similar lookups use one run_background with a "
                        "batch scraper tool, NOT N subagents — see skill "
                        "description."
                    ),
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
                "Error: dispatch_subagents needs ≥2 distinct tasks. For a "
                "single task, call `run_background(instruction=...)` — it "
                "runs ONE worker in the background, keeps the brain free, "
                "and Telegram-pushes when done. Use `delegate(...)` only "
                "when you need the merged answer back in THIS same turn."
            )
        if len(raw_tasks) > 5:
            return (
                f"Error: dispatch_subagents capped at 5 (you asked for "
                f"{len(raw_tasks)}). For ≥6 similar lookups, fire ONE "
                f"`run_background` task that calls a batch scraper tool "
                f"(`mcp_scraper_batch_search_google` for queries, "
                f"`mcp_scraper_batch_crawl` for URLs, "
                f"`mcp_scraper_search_and_crawl` for query→page→extract). "
                f"That uses one worker instead of N cold-starting context "
                f"copies most of which will time out — see prior session "
                f"logs where 21-subagent fan-outs returned zero results."
            )

        # NOTE: prior versions of this skill rejected ≥3 same-shape
        # tasks ("find email for X / Y / Z") on the theory they were
        # really ONE batch job split N ways. That blocked the
        # legitimate chunked-fan-out pattern (3 workers each batching
        # 7 of 21 items). The 5-cap above is the real safeguard
        # against the original 21-way explosion. Worker-loop drift in
        # the foreground brain (the user-facing failure mode) is
        # caught separately by the mid-turn pivot detector in
        # ``lazyclaw/runtime/agent.py`` (see ``detect_inline_pivot``)
        # — that nudges the brain to dispatch when it iterates inline.
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

        # RC2 — set up auto-consolidation when a lane-queue-backed TaskRunner
        # is available. The subagents' results are bucketed under a shared
        # group; when the last settles, TaskRunner fires ONE synthetic brain
        # turn that folds them into a single reply on the originating chat.
        # Without this the brain's "I'll fold results into my next reply"
        # promise was never fulfilled (2026-05-29 "Chek my whats up" bug).
        _tr = self._task_runner
        _group_id: str | None = None
        if _tr is not None and getattr(_tr, "_lane_queue", None) is not None:
            _group_id = uuid.uuid4().hex[:12]

        def _on_register(ids: list[str]) -> None:
            if _group_id:
                _tr.register_subagent_fanout(
                    _group_id, user_id, ids,
                    self._callback, self._chat_session_id,
                )

        def _on_settle(task_id: str, res) -> None:
            if _group_id:
                _tr.record_subagent_result(
                    task_id,
                    name=f"{res.agent_type.value} subagent",
                    success=res.success,
                    result=res.result or "",
                    error=res.error or "",
                    duration_ms=res.duration_ms,
                )

        task_ids = await dispatcher.submit_async(
            configs, user_id,
            on_register=_on_register if _group_id else None,
            on_settle=_on_settle if _group_id else None,
            fanout_group_id=_group_id,
        )
        breakdown = ", ".join(
            f"{c.agent_type.value}: {c.task[:50]}" for c in configs
        )
        if _group_id:
            _fold = (
                "When ALL subagents settle, their results are automatically "
                "consolidated into ONE follow-up reply for the user — you do "
                "NOT need to chase them. For THIS turn, reply with a brief "
                "honest status (e.g. 'Started 2 subagents — I'll send the "
                "consolidated result shortly')."
            )
        else:
            _fold = (
                "They stream results back as `background_done` events you "
                "absorb on later turns. Reply to the user with a short "
                "status — DO NOT wait for results in this turn."
            )
        return (
            f"Dispatched {len(task_ids)} subagents in the background "
            f"(lane='subagent'). They appear in the Activity panel. "
            f"{_fold}\n"
            f"Task IDs: {', '.join(task_ids)}\n"
            f"Tasks: {breakdown}"
        )
