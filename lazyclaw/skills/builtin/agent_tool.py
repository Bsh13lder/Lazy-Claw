"""Unified `agent` dispatch skill — Claude Code dispatcher pattern.

ONE brain-facing tool replaces the delegate / dispatch_subagents /
run_background 3-way choice:

    agent(agent_type, task, run_in_background=false, timeout=120)

Sync (default): runs the specialist loop inline and returns its result
as THIS tool call's result. The TAOR loop executes independent tool
calls concurrently, so N `agent` calls in one assistant message run in
parallel (throttled by a shared per-loop semaphore) and the brain
synthesizes every result in the same turn — no consolidation turn.

Background: routes to TaskRunner (fresh full Agent, up to 10 concurrent)
with the per-turn fanout group so siblings consolidate into one reply.

Spec: docs/superpowers/specs/2026-07-07-unified-agent-dispatch-design.md
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING

from lazyclaw.runtime.callbacks import AgentEvent, StepTrackingCallback
from lazyclaw.runtime.dispatcher import _IS_SUBAGENT
from lazyclaw.skills.base import BaseSkill
from lazyclaw.teams.specialist_aliases import (
    resolve_specialist,
    specialist_choices,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.runtime.team_lead import TeamLead
    from lazyclaw.skills.registry import SkillRegistry
    from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)

# Fan-out width per turn — call N+1 gets a clear error, never a silent
# infinite queue. The skill is re-registered fresh each TAOR turn
# (runtime/agent.py), so the counter resets naturally.
MAX_AGENTS_PER_TURN = 15

# Sync-result cap. Subagent results are load-bearing synthesis input —
# per the 2026-05-25 truncation-confabulation incident the cap is
# generous and the cut is ALWAYS marked. runtime/agent.py mirrors this
# in _MAX_TOOL_RESULT_CHARS_AGENT so its generic capper never re-chops.
MAX_AGENT_RESULT_CHARS = 12000

_DEFAULT_TIMEOUT_S = 120
_MAX_TIMEOUT_S = 600
_MIN_TIMEOUT_S = 10


def _agent_concurrency() -> int:
    raw = os.environ.get("LAZYCLAW_DISPATCH_CONCURRENCY")
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return n
        except ValueError:
            pass
    return 6


# One semaphore per event loop (the server has one loop; tests spin
# fresh loops). Keyed by loop id so a dead loop's entry is just inert.
_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _loop_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _SEMAPHORES.get(loop_id)
    if sem is None:
        sem = asyncio.Semaphore(_agent_concurrency())
        _SEMAPHORES[loop_id] = sem
    return sem


def clip_agent_result(text: str, cap: int = MAX_AGENT_RESULT_CHARS) -> str:
    """Clip with an EXPLICIT marker so F1/triage tooling sees the cut."""
    if len(text) <= cap:
        return text
    dropped = len(text) - cap
    return f"{text[:cap]}\n[truncated {dropped} chars]"


class AgentDispatchSkill(BaseSkill):
    """Dispatch a task to a sub-agent (sync fan-out or background)."""

    def __init__(
        self,
        config: Config | None = None,
        registry: SkillRegistry | None = None,
        eco_router: EcoRouter | None = None,
        permission_checker=None,
        callback: AgentCallback | None = None,
        team_lead: TeamLead | None = None,
        task_runner=None,
        chat_session_id: str | None = None,
        fanout_group_id: str | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._callback = callback
        self._team_lead = team_lead
        self._task_runner = task_runner
        self._chat_session_id = chat_session_id
        self._fanout_group_id = fanout_group_id
        self._caller_depth = 0
        self._calls_this_turn = 0

    # Skill-executor ceiling — real budget is the per-call wait_for.
    timeout = _MAX_TIMEOUT_S + 60

    @property
    def name(self) -> str:
        return "agent"

    @property
    def display_name(self) -> str:
        return "Dispatch Agent"

    @property
    def description(self) -> str:
        return (
            "Dispatch a task to a sub-agent. DEFAULT (run_in_background="
            "false): the agent runs NOW and this call returns its result — "
            "call `agent` MULTIPLE TIMES IN ONE MESSAGE to fan out parallel "
            "agents; all results come back this turn and you synthesize ONE "
            "reply. Set run_in_background=true ONLY for slow work (>2 min: "
            "bulk scrapes, long browser flows) — you get a task id now and "
            "one consolidated report later. Agent types: explore (read-only "
            "research), general_purpose (all tools, multi-step), or a domain "
            "specialist (browser, upwork, email, notes, tasks, documents, "
            "...). Each agent has its own tools and NO chat history — write "
            "self-contained tasks with every name/URL/number it needs."
        )

    @property
    def category(self) -> str:
        return "orchestration"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "enum": specialist_choices(),
                    "description": (
                        "explore = read-only research/search. "
                        "general_purpose = multi-step task, full tools. "
                        "Domain names (browser, upwork, email, ...) = "
                        "scoped specialist."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Complete, self-contained instruction. The agent "
                        "has NO chat history — include every URL, name, "
                        "number, and the success criteria."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "true = slow work; task id now, consolidated "
                        "report later. false (default) = result returns "
                        "in THIS turn."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "default": _DEFAULT_TIMEOUT_S,
                    "minimum": _MIN_TIMEOUT_S,
                    "maximum": _MAX_TIMEOUT_S,
                    "description": "Sync budget in seconds (default 120).",
                },
            },
            "required": ["agent_type", "task"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        agent_type = (params.get("agent_type") or "").strip()
        task = (params.get("task") or "").strip()
        run_bg = bool(params.get("run_in_background", False))
        try:
            timeout_s = int(params.get("timeout", _DEFAULT_TIMEOUT_S))
        except (TypeError, ValueError):
            timeout_s = _DEFAULT_TIMEOUT_S
        timeout_s = max(_MIN_TIMEOUT_S, min(timeout_s, _MAX_TIMEOUT_S))

        if not task:
            return "Error: task is required"

        spec = resolve_specialist(agent_type)
        if spec is None:
            return (
                f"Unknown agent_type '{agent_type}'. "
                f"Valid: {', '.join(specialist_choices())}"
            )

        if _IS_SUBAGENT.get():
            return (
                "Error: sub-agents cannot spawn sub-agents (single-depth). "
                "Complete the task with your own tools."
            )

        if run_bg:
            return await self._execute_background(user_id, agent_type, task)

        self._calls_this_turn += 1
        if self._calls_this_turn > MAX_AGENTS_PER_TURN:
            return (
                f"Error: fan-out cap reached ({MAX_AGENTS_PER_TURN} agents "
                f"this turn). Synthesize what you have, or use "
                f"run_in_background=true for the remainder."
            )

        return await self._execute_sync(
            user_id, spec, agent_type, task, timeout_s,
        )

    # ── Sync path ────────────────────────────────────────────────────

    async def _execute_sync(
        self,
        user_id: str,
        spec: SpecialistConfig,
        agent_type: str,
        task: str,
        timeout_s: int,
    ) -> str:
        from lazyclaw.teams import runner as team_runner

        task_id = f"agent-{uuid.uuid4().hex[:8]}"
        if self._team_lead is not None:
            try:
                self._team_lead.register(
                    task_id=task_id,
                    name=spec.name,
                    description=task[:80],
                    lane="subagent",
                    instruction_full=task,
                    user_id=user_id,
                )
            except Exception:
                logger.debug(
                    "team_lead.register failed for %s", task_id, exc_info=True,
                )

        wrapped_callback = self._callback
        if self._team_lead is not None and self._callback is not None:
            wrapped_callback = StepTrackingCallback(
                inner=self._callback,
                team_lead=self._team_lead,
                task_id=task_id,
            )

        if self._callback:
            await self._callback.on_event(AgentEvent(
                "specialist_start",
                spec.name,
                {"specialist": spec.name, "task": task[:200]},
            ))

        started = time.monotonic()

        async def _acquire_and_run():
            async with _loop_semaphore():
                # Single-depth: mark this execution context as a subagent so
                # anything the specialist reaches (including a custom
                # specialist that allowlists `agent`) cannot re-dispatch.
                token = _IS_SUBAGENT.set(True)
                try:
                    return await team_runner.run_specialist(
                        user_id=user_id,
                        specialist=spec,
                        task=task,
                        registry=self._registry,
                        eco_router=self._eco_router,
                        permission_checker=self._permission_checker,
                        callback=wrapped_callback,
                        task_id=task_id,
                    )
                finally:
                    _IS_SUBAGENT.reset(token)

        try:
            result = await asyncio.wait_for(
                _acquire_and_run(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            self._mark_failed(task_id, f"timeout after {timeout_s}s")
            return (
                f"[agent:{agent_type}] TIMEOUT after {timeout_s}s — the "
                f"agent was cancelled. For slow work retry with "
                f"run_in_background=true."
            )
        except Exception as exc:
            logger.exception("agent %s (%s) crashed", task_id, agent_type)
            self._mark_failed(task_id, str(exc))
            return f"[agent:{agent_type}] FAILED: {exc}"

        elapsed = int(time.monotonic() - started)
        body = clip_agent_result(result.result or "")

        if self._team_lead is not None:
            try:
                if result.success:
                    self._team_lead.complete(task_id, result_preview=body[:200])
                else:
                    self._team_lead.fail(task_id, error=result.error or "")
            except Exception:
                logger.debug("team_lead settle failed", exc_info=True)

        status = (
            "completed" if result.success
            else f"FAILED: {result.error or 'unknown error'}"
        )
        return f"[agent:{agent_type}] {status} in {elapsed}s\n{body}"

    def _mark_failed(self, task_id: str, error: str) -> None:
        if self._team_lead is not None:
            try:
                self._team_lead.fail(task_id, error=error)
            except Exception:
                logger.debug("team_lead.fail failed", exc_info=True)

    # ── Background path (Task 5 fills tests) ────────────────────────

    async def _execute_background(
        self, user_id: str, agent_type: str, task: str,
    ) -> str:
        if not self._task_runner:
            return "Error: background task runner not configured"
        try:
            task_id = await self._task_runner.submit(
                user_id=user_id,
                instruction=task,
                name=f"agent:{agent_type}",
                callback=self._callback,
                source="brain",
                fanout_group_id=self._fanout_group_id,
                chat_session_id=self._chat_session_id,
                caller_depth=self._caller_depth,
            )
        except RuntimeError as exc:
            return f"Cannot start background agent: {exc}"
        return (
            f"Background agent '{agent_type}' started (id: {task_id[:8]}). "
            f"One consolidated report will follow when it finishes."
        )
