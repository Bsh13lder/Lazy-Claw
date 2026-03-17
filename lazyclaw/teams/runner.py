"""Specialist runner — executes a single specialist as a mini agent loop.

Reuses the same LLM routing, tool execution, and permission checking
as the main agent, but with a filtered skill set and specialist-specific
system prompt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


@dataclass(frozen=True)
class SpecialistResult:
    """Immutable result from a specialist run."""

    agent_name: str
    task: str
    result: str
    tools_used: tuple[str, ...]
    model_used: str
    duration_ms: int
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
                    success=False,
                    error="Cancelled by user",
                )

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

            response = await eco_router.chat(
                messages, user_id=user_id, model=specialist.preferred_model, **kwargs
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
                )

            # Process tool calls
            assistant_msg = LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_msg)

            for tc in response.tool_calls:
                # Only execute if skill is in allowed list
                if tc.name not in specialist.allowed_skills:
                    tool_result = f"Error: Tool '{tc.name}' is not available to {specialist.display_name}."
                else:
                    tool_result = await executor.execute(tc, user_id)

                    # If approval required, note it but don't block
                    if isinstance(tool_result, str) and tool_result.startswith(APPROVAL_PREFIX):
                        tool_result = (
                            f"Tool '{tc.name}' requires user approval and cannot be used "
                            f"in team mode right now. Skip this tool and work with what you have."
                        )

                    tools_used.append(tc.name)

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
            success=False,
            error=str(exc),
        )
