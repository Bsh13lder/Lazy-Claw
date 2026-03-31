"""Team executor — runs multiple specialists in parallel.

Uses asyncio.gather with a semaphore for concurrency control
and per-specialist timeouts. Failed specialists return error
results without blocking others.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from lazyclaw.config import load_config
from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.teams.learning import MIN_STEPS_FOR_LEARNING, save_browser_learnings
from lazyclaw.teams.runner import SpecialistResult, run_specialist
from lazyclaw.teams.specialist import BROWSER_SPECIALIST, SpecialistConfig

logger = logging.getLogger(__name__)

# prevent GC from cancelling fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


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
    tab_manager=None,
) -> SpecialistResult:
    """Run a single specialist with timeout and semaphore control.

    If tab_manager is provided and the specialist is browser, acquires
    a tab before execution and guarantees release via try/finally.
    """
    tab_context = None
    async with semaphore:
        if callback:
            await callback.on_event(AgentEvent(
                "specialist_start", task.specialist.name,
                {"specialist": task.specialist.name, "task": task.instruction[:100]},
            ))
        try:
            # Acquire tab for browser specialist if TabManager is available
            if tab_manager and task.specialist.name == BROWSER_SPECIALIST.name:
                from lazyclaw.browser.tab_manager import TabManager
                if isinstance(tab_manager, TabManager):
                    tab_context = await tab_manager.acquire(
                        "about:blank", task.specialist.name,
                    )

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
                    tab_context=tab_context,
                ),
                timeout=timeout,
            )
            # Fire-and-forget: save browser learnings to site memory
            if (
                task.specialist.name == BROWSER_SPECIALIST.name
                and len(result.step_history) >= MIN_STEPS_FOR_LEARNING
            ):
                bg_task = asyncio.create_task(save_browser_learnings(
                    config=load_config(),
                    user_id=user_id,
                    step_history=result.step_history,
                    task=task.instruction,
                    success=result.success,
                    error=result.error,
                ))
                _background_tasks.add(bg_task)
                bg_task.add_done_callback(_background_tasks.discard)
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
        finally:
            # Guarantee tab release on timeout, cancel, or crash
            if tab_context and tab_manager:
                try:
                    await tab_manager.release(tab_context.domain)
                except Exception:
                    logger.debug("Tab release failed for %s", task.specialist.name)


async def execute_team(
    tasks: list[TeamTask],
    user_id: str,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker,
    max_parallel: int = 10,
    timeout: int = 120,
    callback=None,
    cancel_token=None,
    tab_manager=None,
) -> list[SpecialistResult]:
    """Run multiple specialists in parallel.

    Returns results in the same order as the input tasks.
    Failed or timed-out specialists return error SpecialistResults.
    If tab_manager is provided, browser specialists get isolated tabs
    with guaranteed release via try/finally.
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
            tab_manager=tab_manager,
        )
        for task in tasks
    ]

    results = await asyncio.gather(*coroutines)
    return list(results)
