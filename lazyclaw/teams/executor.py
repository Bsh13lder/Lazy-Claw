"""Team executor — runs multiple specialists in parallel.

Uses asyncio.gather with a semaphore for concurrency control
and per-specialist timeouts. Failed specialists return error
results without blocking others.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.teams.runner import SpecialistResult, run_specialist
from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamTask:
    """Immutable task assignment for a specialist."""

    specialist: SpecialistConfig
    instruction: str


async def _run_with_timeout(
    user_id: str,
    task: TeamTask,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker,
    timeout: int,
    semaphore: asyncio.Semaphore,
    callback=None,
    cancel_token=None,
) -> SpecialistResult:
    """Run a single specialist with timeout and semaphore control."""
    async with semaphore:
        if callback:
            await callback.on_event(AgentEvent(
                "specialist_start", task.specialist.name,
                {"specialist": task.specialist.name, "task": task.instruction[:100]},
            ))
        try:
            result = await asyncio.wait_for(
                run_specialist(
                    user_id=user_id,
                    specialist=task.specialist,
                    task=task.instruction,
                    registry=registry,
                    eco_router=eco_router,
                    permission_checker=permission_checker,
                    callback=callback,
                    cancel_token=cancel_token,
                ),
                timeout=timeout,
            )
            if callback:
                await callback.on_event(AgentEvent(
                    "specialist_done", result.agent_name,
                    {"specialist": result.agent_name, "duration_ms": result.duration_ms,
                     "success": result.success, "tools_used": list(result.tools_used),
                     "error": result.error},
                ))
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "Specialist %s timed out after %ds",
                task.specialist.name, timeout,
            )
            error_result = SpecialistResult(
                agent_name=task.specialist.name,
                task=task.instruction,
                result="",
                tools_used=(),
                model_used="unknown",
                duration_ms=timeout * 1000,
                success=False,
                error=f"Timed out after {timeout} seconds",
            )
            if callback:
                await callback.on_event(AgentEvent(
                    "specialist_done", task.specialist.name,
                    {"specialist": task.specialist.name, "duration_ms": timeout * 1000,
                     "success": False, "tools_used": [],
                     "error": error_result.error},
                ))
            return error_result
        except Exception as exc:
            logger.error("Specialist %s crashed: %s", task.specialist.name, exc)
            error_result = SpecialistResult(
                agent_name=task.specialist.name,
                task=task.instruction,
                result="",
                tools_used=(),
                model_used="unknown",
                duration_ms=0,
                success=False,
                error=str(exc),
            )
            if callback:
                await callback.on_event(AgentEvent(
                    "specialist_done", task.specialist.name,
                    {"specialist": task.specialist.name, "duration_ms": 0,
                     "success": False, "tools_used": [],
                     "error": str(exc)},
                ))
            return error_result


async def execute_team(
    tasks: list[TeamTask],
    user_id: str,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker,
    max_parallel: int = 3,
    timeout: int = 120,
    callback=None,
    cancel_token=None,
) -> list[SpecialistResult]:
    """Run multiple specialists in parallel.

    Returns results in the same order as the input tasks.
    Failed or timed-out specialists return error SpecialistResults.
    """
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(max_parallel)

    coroutines = [
        _run_with_timeout(
            user_id=user_id,
            task=task,
            registry=registry,
            eco_router=eco_router,
            permission_checker=permission_checker,
            timeout=timeout,
            semaphore=semaphore,
            callback=callback,
            cancel_token=cancel_token,
        )
        for task in tasks
    ]

    results = await asyncio.gather(*coroutines)
    return list(results)
