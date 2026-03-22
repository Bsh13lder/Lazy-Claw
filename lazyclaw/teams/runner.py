"""Specialist runner — executes a single specialist as a mini agent loop.

Reuses the same LLM routing, tool execution, and permission checking
as the main agent, but with a filtered skill set and specialist-specific
system prompt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from lazyclaw.config import load_config
from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.stuck_detector import detect_stuck
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.teams.learning import StepEntry
from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 50
_NUDGE_AT = int(MAX_ITERATIONS * 0.8)  # 40


@dataclass(frozen=True)
class SpecialistResult:
    """Immutable result from a specialist run."""

    agent_name: str
    task: str
    result: str
    tools_used: tuple[str, ...]
    model_used: str
    duration_ms: int
    step_history: tuple[StepEntry, ...] = ()
    success: bool = True
    error: str | None = None


def _filter_tools(registry: SkillRegistry, allowed: tuple[str, ...]) -> list[dict]:
    """Return OpenAI-format tool list filtered to allowed skill names."""
    all_tools = registry.list_tools()
    allowed_set = set(allowed)
    return [t for t in all_tools if t["function"]["name"] in allowed_set]


async def run_specialist(
    user_id: str,
    specialist: SpecialistConfig,
    task: str,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker,
    callback=None,
    cancel_token=None,
    tab_context=None,
) -> SpecialistResult:
    """Run a specialist agent loop for a single task.

    Uses the same agentic pattern as Agent.process_message but with:
    - Specialist-specific system prompt
    - Filtered tool set (only specialist.allowed_skills)
    - No conversation history (fresh context per task)
    - No message persistence (team lead handles storage)
    """
    start_time = time.monotonic()
    tools_used: list[str] = []
    step_history: list[StepEntry] = []

    # Build filtered tools
    filtered_tools = _filter_tools(registry, specialist.allowed_skills)

    # Build executor (reuses same registry + permission checker)
    executor = ToolExecutor(registry, permission_checker=permission_checker)

    # System prompt = specialist prompt + task
    system_prompt = (
        f"{specialist.system_prompt}\n\n"
        f"---\n\n"
        f"Your task:\n{task}\n\n"
        f"Complete this task using your available tools. "
        f"When done, provide a clear summary of your findings or results."
    )

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=task),
    ]

    model_used = specialist.preferred_model or "default"

    # Stuck detection state — tracks tool names and results across iterations
    _tool_history: list[str] = []
    _tool_results: list[str] = []

    try:
        for _iteration in range(MAX_ITERATIONS):
            if cancel_token and cancel_token.is_cancelled:
                duration = int((time.monotonic() - start_time) * 1000)
                return SpecialistResult(
                    agent_name=specialist.name,
                    task=task,
                    result="",
                    tools_used=tuple(tools_used),
                    model_used=model_used,
                    duration_ms=duration,
                    step_history=tuple(step_history),
                    success=False,
                    error="Cancelled by user",
                )

            # Running-long nudge at 80% of cap
            if _iteration == _NUDGE_AT:
                messages.append(LLMMessage(
                    role="system",
                    content=(
                        "You've been working for a while. Wrap up: summarize what "
                        "you've done and what's left. If task is incomplete, report "
                        "partial results — don't keep going silently."
                    ),
                ))
                logger.info(
                    "Specialist %s: nudge at iteration %d/%d",
                    specialist.name, _iteration + 1, MAX_ITERATIONS,
                )

            # Prune old tool results to save tokens
            if _iteration > 0:
                from lazyclaw.runtime.agent import _prune_old_tool_results
                messages = _prune_old_tool_results(messages, keep_last_n=2)

            # Fire specialist thinking event for observability
            if callback:
                await callback.on_event(AgentEvent(
                    "specialist_thinking",
                    f"{specialist.name} thinking (step {_iteration + 1})",
                    {"specialist": specialist.name, "iteration": _iteration + 1},
                ))

            kwargs: dict = {}
            if filtered_tools:
                kwargs["tools"] = filtered_tools

            # Specialists are executors — they use worker_model by default.
            # "smart" alias kept for future use but resolves to brain_model only
            # when explicitly needed. Default: all specialists use worker (Haiku).
            _cfg = load_config()
            _specialist_model = specialist.preferred_model
            if _specialist_model == "smart":
                _specialist_model = _cfg.worker_model
            elif _specialist_model == "fast" or not _specialist_model:
                _specialist_model = _cfg.worker_model
            logger.info("Specialist %s iteration %d: calling %s", specialist.name, _iteration + 1, _specialist_model)
            response = await eco_router.chat(
                messages, user_id=user_id, model=_specialist_model, **kwargs
            )
            model_used = response.model or model_used

            if not response.tool_calls:
                # Final response — specialist is done
                duration = int((time.monotonic() - start_time) * 1000)
                return SpecialistResult(
                    agent_name=specialist.name,
                    task=task,
                    result=response.content or "",
                    tools_used=tuple(tools_used),
                    model_used=model_used,
                    duration_ms=duration,
                    step_history=tuple(step_history),
                )

            # Process tool calls
            assistant_msg = LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_msg)

            for tc in response.tool_calls:
                # Log what the specialist is calling (critical for debugging)
                _args_summary = {k: (str(v)[:80] if isinstance(v, str) else v)
                                 for k, v in (tc.arguments or {}).items()
                                 if not k.startswith("_")}
                logger.info(
                    "Specialist %s step %d: %s(%s)",
                    specialist.name, _iteration + 1, tc.name, _args_summary,
                )

                # Only execute if skill is in allowed list
                if tc.name not in specialist.allowed_skills:
                    tool_result = f"Error: Tool '{tc.name}' is not available to {specialist.display_name}."
                else:
                    # Inject TabContext for browser isolation (immutable — new ToolCall)
                    exec_tc = tc
                    if tab_context and tc.name == "browser":
                        exec_tc = ToolCall(
                            id=tc.id,
                            name=tc.name,
                            arguments={**tc.arguments, "_tab_context": tab_context},
                        )
                    tool_result = await executor.execute(exec_tc, user_id, callback=callback)

                    # If approval required, note it but don't block
                    if isinstance(tool_result, str) and tool_result.startswith(APPROVAL_PREFIX):
                        tool_result = (
                            f"Tool '{tc.name}' requires user approval and cannot be used "
                            f"in team mode right now. Skip this tool and work with what you have."
                        )

                    tools_used.append(tc.name)
                    _is_error = isinstance(tool_result, str) and tool_result.startswith("Error")
                    step_history.append(StepEntry(
                        tool_name=tc.name,
                        action=(tc.arguments or {}).get("action"),
                        target=(tc.arguments or {}).get("target"),
                        success=not _is_error,
                        error_snippet=tool_result[:200] if _is_error else "",
                        iteration=_iteration,
                    ))
                    _result_len = len(tool_result) if isinstance(tool_result, str) else 0
                    if _result_len > 500:
                        logger.debug(
                            "Specialist %s: %s returned %d chars",
                            specialist.name, tc.name, _result_len,
                        )

                    if callback:
                        await callback.on_event(AgentEvent(
                            "specialist_tool",
                            tc.name,
                            {"specialist": specialist.name, "tool": tc.name},
                        ))

                messages.append(LLMMessage(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tc.id,
                ))

                # Track for stuck detection
                _tool_history.append(tc.name)
                _tool_results.append(
                    tool_result if isinstance(tool_result, str) else str(tool_result)
                )

            # ── Stuck detection after processing all tool calls ──
            stuck = detect_stuck(_tool_history, _tool_results, _tool_results[-1] if _tool_results else None)
            if stuck:
                duration = int((time.monotonic() - start_time) * 1000)
                logger.warning(
                    "Specialist %s stuck: %s (%s)",
                    specialist.name, stuck.reason, stuck.context,
                )
                return SpecialistResult(
                    agent_name=specialist.name,
                    task=task,
                    result=(
                        f"[Stuck: {stuck.reason}] {stuck.context}\n\n"
                        f"Completed {len(tools_used)} tool calls before getting stuck."
                    ),
                    tools_used=tuple(tools_used),
                    model_used=model_used,
                    duration_ms=duration,
                    step_history=tuple(step_history),
                    success=False,
                    error=stuck.context,
                )

        # Max iterations reached
        duration = int((time.monotonic() - start_time) * 1000)
        last_content = messages[-1].content if messages else "Max iterations reached."
        return SpecialistResult(
            agent_name=specialist.name,
            task=task,
            result=f"[Reached max iterations] {last_content}",
            tools_used=tuple(tools_used),
            model_used=model_used,
            duration_ms=duration,
            step_history=tuple(step_history),
        )

    except Exception as exc:
        duration = int((time.monotonic() - start_time) * 1000)
        logger.error("Specialist %s failed: %s", specialist.name, exc)
        return SpecialistResult(
            agent_name=specialist.name,
            task=task,
            result="",
            tools_used=tuple(tools_used),
            model_used=model_used,
            duration_ms=duration,
            step_history=tuple(step_history),
            success=False,
            error=str(exc),
        )
