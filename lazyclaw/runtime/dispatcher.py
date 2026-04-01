"""Subagent dispatch system — inspired by Claude Code's multi-agent architecture.

Three agent types:
  EXPLORE         — read-only, cheap model, isolated context (research/search)
  GENERAL_PURPOSE — full tool access, primary model (complex multi-step tasks)
  SPECIALIST      — caller-configured scoped tools (browser, data, code)

Dispatch rules:
  • 3+ independent subtasks → spawn parallel subagents
  • Research/search tasks   → EXPLORE (cheap, fast, safe)
  • State mutations         → GENERAL_PURPOSE (careful)
  • Single-depth only       — subagents cannot spawn subagents
  • Isolated context        — no parent conversation history
  • Structured summaries    — results returned as SubagentResult
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# ── Single-depth enforcement ────────────────────────────────────────────
# Set to True inside _run_subagent coroutines so nested dispatch is blocked.
_IS_SUBAGENT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "lazyclaw_is_subagent", default=False,
)


class AgentType(str, Enum):
    EXPLORE = "explore"                  # Read-only, cheap model
    GENERAL_PURPOSE = "general_purpose"  # Full access, primary model
    SPECIALIST = "specialist"            # Caller-scoped tools


# Tools available to EXPLORE agents — read-only, no state mutations
_EXPLORE_TOOLS: frozenset[str] = frozenset({
    "web_search", "search_tools", "recall_memories",
    "read_file", "list_directory", "browser",
})

# Tools excluded from GENERAL_PURPOSE agents to prevent recursive dispatch
_GENERAL_PURPOSE_EXCLUDED: frozenset[str] = frozenset({
    "dispatch_subagents", "delegate",
})

_EXPLORE_SYSTEM_PROMPT = (
    "You are a read-only research and exploration agent. Your job is to gather "
    "information — search the web, read files, inspect pages — and return a clear, "
    "structured summary of your findings. You MUST NOT modify any state: no writes, "
    "no sends, no creates, no deletes. Focus on thorough research and return "
    "actionable results with sources cited where possible."
)

_GENERAL_PURPOSE_SYSTEM_PROMPT = (
    "You are a general-purpose agent handling a delegated subtask. Complete the task "
    "fully using whatever tools are available. Return a clear, structured summary of "
    "what you did and the outcome."
)

_SPECIALIST_SYSTEM_PROMPT = (
    "You are a specialist agent with a scoped tool set. Use your available tools to "
    "complete the assigned task fully. Return a clear, structured summary of the outcome."
)


@dataclass(frozen=True)
class SubagentConfig:
    """Immutable configuration for a single subagent invocation."""

    agent_type: AgentType
    task: str
    tool_names: tuple[str, ...] | None = None  # None → type defaults
    timeout: int = 60                           # seconds per subagent


@dataclass(frozen=True)
class SubagentResult:
    """Immutable result from a completed subagent run."""

    agent_type: AgentType
    task: str
    result: str
    success: bool
    tokens_used: int
    duration_ms: int
    error: str | None = None


class AgentDispatcher:
    """Manages subagent lifecycle. Enforces single-depth dispatch.

    Usage::

        dispatcher = AgentDispatcher(config, eco_router, registry, checker)
        results = await dispatcher.dispatch([
            SubagentConfig(AgentType.EXPLORE, "research topic X"),
            SubagentConfig(AgentType.EXPLORE, "find docs for Y"),
        ], user_id=user_id)
    """

    def __init__(
        self,
        config: Config,
        eco_router: EcoRouter,
        registry: SkillRegistry,
        permission_checker,
    ) -> None:
        self._config = config
        self._eco_router = eco_router
        self._registry = registry
        self._permission_checker = permission_checker

    async def dispatch(
        self,
        configs: list[SubagentConfig],
        user_id: str,
    ) -> list[SubagentResult]:
        """Dispatch subagents in parallel. Returns results in input order."""
        if not configs:
            return []
        tasks = [self._run_subagent(cfg, user_id) for cfg in configs]
        return list(await asyncio.gather(*tasks))

    async def _run_subagent(
        self,
        cfg: SubagentConfig,
        user_id: str,
    ) -> SubagentResult:
        """Run a single subagent. Sets _IS_SUBAGENT to block recursive dispatch."""
        from lazyclaw.teams.runner import run_specialist

        # Mark context as subagent — blocks nested dispatch_subagents calls
        token = _IS_SUBAGENT.set(True)
        start = time.monotonic()

        try:
            spec = self._make_specialist(cfg)
            result = await asyncio.wait_for(
                run_specialist(
                    user_id=user_id,
                    specialist=spec,
                    task=cfg.task,
                    registry=self._registry,
                    eco_router=self._eco_router,
                    permission_checker=self._permission_checker,
                ),
                timeout=cfg.timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return SubagentResult(
                agent_type=cfg.agent_type,
                task=cfg.task,
                result=result.result,
                success=result.success,
                tokens_used=0,  # SpecialistResult has no usage field yet
                duration_ms=duration_ms,
                error=result.error,
            )

        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "Subagent %s timed out after %ds: %.60s",
                cfg.agent_type.value, cfg.timeout, cfg.task,
            )
            return SubagentResult(
                agent_type=cfg.agent_type,
                task=cfg.task,
                result="",
                success=False,
                tokens_used=0,
                duration_ms=duration_ms,
                error=f"Timed out after {cfg.timeout}s",
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "Subagent %s failed: %s — %.60s",
                cfg.agent_type.value, exc, cfg.task,
            )
            return SubagentResult(
                agent_type=cfg.agent_type,
                task=cfg.task,
                result="",
                success=False,
                tokens_used=0,
                duration_ms=duration_ms,
                error=str(exc),
            )

        finally:
            _IS_SUBAGENT.reset(token)

    def _make_specialist(self, cfg: SubagentConfig):
        """Build a SpecialistConfig from a SubagentConfig."""
        from lazyclaw.teams.specialist import SpecialistConfig

        if cfg.agent_type == AgentType.EXPLORE:
            allowed = (
                tuple(sorted(cfg.tool_names))
                if cfg.tool_names
                else tuple(sorted(_EXPLORE_TOOLS))
            )
            return SpecialistConfig(
                name="explore_agent",
                display_name="Explore Agent",
                system_prompt=_EXPLORE_SYSTEM_PROMPT,
                allowed_skills=allowed,
                preferred_model="worker",
                is_builtin=True,
            )

        if cfg.agent_type == AgentType.GENERAL_PURPOSE:
            if cfg.tool_names:
                allowed = tuple(sorted(cfg.tool_names))
            else:
                # All registered tools except dispatch/delegate to prevent recursion
                all_names = {
                    t["function"]["name"]
                    for t in self._registry.list_tools()
                }
                allowed = tuple(sorted(all_names - _GENERAL_PURPOSE_EXCLUDED))
            return SpecialistConfig(
                name="general_purpose_agent",
                display_name="General-Purpose Agent",
                system_prompt=_GENERAL_PURPOSE_SYSTEM_PROMPT,
                allowed_skills=allowed,
                preferred_model="brain",
                is_builtin=True,
            )

        # SPECIALIST — caller-specified tools required
        if not cfg.tool_names:
            raise ValueError(
                "SPECIALIST agent requires tool_names to be specified"
            )
        return SpecialistConfig(
            name="specialist_agent",
            display_name="Specialist Agent",
            system_prompt=_SPECIALIST_SYSTEM_PROMPT,
            allowed_skills=tuple(sorted(cfg.tool_names)),
            preferred_model="worker",
            is_builtin=True,
        )
