"""Delegate skill — dispatches sub-tasks to specialist agents.

Replaces the separate team lead LLM analysis call. The main agent
naturally decides when to delegate by calling this tool, saving
1-2 LLM calls per delegation. Inspired by NanoClaw's inline
agent dispatch pattern.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lazyclaw.skills.base import BaseSkill
from lazyclaw.teams.specialist import (
    BROWSER_SPECIALIST,
    CODE_SPECIALIST,
    RESEARCH_SPECIALIST,
    SpecialistConfig,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Short name → specialist config
_SPECIALIST_MAP: dict[str, SpecialistConfig] = {
    "browser": BROWSER_SPECIALIST,
    "research": RESEARCH_SPECIALIST,
    "code": CODE_SPECIALIST,
}


class DelegateSkill(BaseSkill):
    """Delegate a sub-task to a specialist agent with specific tools.

    The specialist runs as an independent agentic loop with its own
    tool set and returns the result. Use when a task needs browser
    automation, web research with file access, or code generation.
    """

    def __init__(
        self,
        config: Config,
        registry: SkillRegistry,
        eco_router: EcoRouter,
        permission_checker=None,
        callback: AgentCallback | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._callback = callback

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def display_name(self) -> str:
        return "Delegate to Specialist"

    @property
    def description(self) -> str:
        return (
            "Delegate a sub-task to a specialist agent. Use when you need "
            "browser automation (navigate, click, read pages), web research "
            "(search + read files), or code/skill creation. The specialist "
            "has tools you don't — it runs independently and returns the result."
        )

    @property
    def category(self) -> str:
        return "general"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "specialist": {
                    "type": "string",
                    "enum": list(_SPECIALIST_MAP.keys()),
                    "description": (
                        "Which specialist: browser (web navigation, page interaction), "
                        "research (web search, file reading, shell commands), "
                        "code (Python, skill creation, calculations)"
                    ),
                },
                "instruction": {
                    "type": "string",
                    "description": "Clear, specific instruction for the specialist",
                },
            },
            "required": ["specialist", "instruction"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.teams.runner import run_specialist

        specialist_key = params.get("specialist", "")
        instruction = params.get("instruction", "")

        if not instruction:
            return "Error: instruction is required"

        spec = _SPECIALIST_MAP.get(specialist_key)
        if not spec:
            available = ", ".join(_SPECIALIST_MAP.keys())
            return f"Unknown specialist '{specialist_key}'. Available: {available}"

        logger.info(
            "Delegating to %s: %s", spec.display_name, instruction[:100],
        )

        result = await run_specialist(
            user_id=user_id,
            specialist=spec,
            task=instruction,
            registry=self._registry,
            eco_router=self._eco_router,
            permission_checker=self._permission_checker,
            callback=self._callback,
        )

        if result.success:
            tools_note = ""
            if result.tools_used:
                tools_note = f" (used: {', '.join(result.tools_used)})"
            return (
                f"[{spec.display_name} completed{tools_note}]\n\n"
                f"{result.result}"
            )

        return f"[{spec.display_name} failed] {result.error}"
