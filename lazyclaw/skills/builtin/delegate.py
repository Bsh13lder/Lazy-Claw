"""Delegate skill — dispatches sub-tasks to specialist agents.

Replaces the separate team lead LLM analysis call. The main agent
naturally decides when to delegate by calling this tool, saving
1-2 LLM calls per delegation. Inspired by NanoClaw's inline
agent dispatch pattern.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from lazyclaw.skills.base import BaseSkill
from lazyclaw.teams.learning import MIN_STEPS_FOR_LEARNING, save_browser_learnings
from lazyclaw.teams.specialist import (
    BROWSER_SPECIALIST,
    CODE_SPECIALIST,
    RESEARCH_SPECIALIST,
    SpecialistConfig,
)

# prevent GC from cancelling fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

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

    # Specialists run multi-step browser loops — 60s default is too short
    timeout = 300

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

        # ── Site knowledge: inject cached knowledge if available ──
        # Only use EXISTING site memory — never block to run research first.
        # The AI is smart enough to figure out websites on its own.
        # Site knowledge is a bonus hint, not a prerequisite.
        enriched_instruction = instruction
        if specialist_key == "browser" and self._config:
            site_knowledge = await self._get_cached_site_knowledge(
                user_id, instruction,
            )
            if site_knowledge:
                enriched_instruction = (
                    f"{instruction}\n\n"
                    f"--- Site Knowledge (hints from previous visits) ---\n{site_knowledge}"
                )

        logger.info(
            "Delegating to %s: %s", spec.display_name, enriched_instruction[:100],
        )

        result = await run_specialist(
            user_id=user_id,
            specialist=spec,
            task=enriched_instruction,
            registry=self._registry,
            eco_router=self._eco_router,
            permission_checker=self._permission_checker,
            callback=self._callback,
        )

        # Fire-and-forget: save browser learnings to site memory
        if specialist_key == "browser" and len(result.step_history) >= MIN_STEPS_FOR_LEARNING:
            task = asyncio.create_task(save_browser_learnings(
                config=self._config,
                user_id=user_id,
                step_history=result.step_history,
                task=enriched_instruction,
                success=result.success,
                error=result.error,
            ))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        if result.success:
            tools_note = ""
            if result.tools_used:
                tools_note = f" (used: {', '.join(result.tools_used)})"
            return (
                f"[{spec.display_name} completed{tools_note}]\n\n"
                f"{result.result}"
            )

        return f"[{spec.display_name} failed] {result.error}"

    # ── Site knowledge ─────────────────────────────────────────────

    # Common domains → short names for search queries
    # Services with MCP connectors excluded — agent uses MCP tools, not browser
    _DOMAIN_HINTS: dict[str, str] = {
        "twitter": "x.com",
        "facebook": "facebook.com",
        "linkedin": "linkedin.com",
        "amazon": "amazon.com",
        "youtube": "youtube.com",
    }

    async def _get_cached_site_knowledge(
        self, user_id: str, instruction: str,
    ) -> str:
        """Return cached site knowledge if available. Never blocks to research.

        The AI specialist is smart enough to figure out websites on its own.
        Cached knowledge is just a bonus from previous successful visits.
        """
        from lazyclaw.browser.site_memory import recall, format_memories_for_context

        domain = self._extract_domain(instruction)
        if not domain:
            return ""

        try:
            memories = await recall(self._config, user_id, f"https://{domain}/")
            if memories:
                knowledge = format_memories_for_context(memories)
                logger.info(
                    "Site knowledge for %s: %d cached hints",
                    domain, sum(len(v) for v in memories.values()),
                )
                return knowledge
        except Exception:
            pass

        return ""

    async def _maybe_research_site(
        self, user_id: str, instruction: str,
    ) -> str:
        """Check site_memory for the target domain. If empty, research first.

        NOTE: No longer called automatically. Kept for manual /research command.
        Returns site knowledge string or empty string.
        """
        from lazyclaw.browser.site_memory import (
            recall, remember, format_memories_for_context,
        )

        # Extract domain from instruction
        domain = self._extract_domain(instruction)
        if not domain:
            return ""

        # Check if we already have site knowledge
        try:
            memories = await recall(self._config, user_id, f"https://{domain}/")
            if memories:
                knowledge = format_memories_for_context(memories)
                logger.info(
                    "Site recon for %s: found %d cached memories",
                    domain, sum(len(v) for v in memories.values()),
                )
                return knowledge
        except Exception as e:
            logger.warning("Failed to recall site memories for %s: %s", domain, e)

        # No knowledge — run web search only (no browser, avoids redirect loops)
        logger.info("Site recon for %s: no cached knowledge, researching...", domain)

        action_hint = instruction[:200]
        research_task = (
            f"Use web_search ONLY (do NOT open browser) to research:\n"
            f"How to do this on {domain} web interface: \"{action_hint}\"\n\n"
            f"Search for and summarize:\n"
            f"1. Is this a single-page app (SPA)? If yes, how to navigate via URL (not typing in search box)\n"
            f"2. Search/filter operators and EXACT URL format (e.g. Gmail: mail.google.com/mail/u/0/#search/category%3Apromotions+older_than%3A30d)\n"
            f"3. Bulk action workflow: how to select all, delete/archive in bulk\n"
            f"4. Common pitfalls (e.g. operators that conflict, features that must be enabled)\n\n"
            f"IMPORTANT: For SPAs, the browser automation uses URL hash navigation, NOT typing in search boxes.\n"
            f"Provide the exact URL patterns with proper encoding (spaces→+, colons→%3A).\n\n"
            f"Do 3-5 searches. Return a concise guide."
        )

        # Use a search-only specialist (no browser) to avoid redirect loops
        _recon_specialist = SpecialistConfig(
            name="site_recon",
            display_name="Site Recon",
            system_prompt=(
                "You are a quick web researcher. Use web_search ONLY — never open a browser.\n"
                "Do EXACTLY 3 targeted searches, then STOP and return your findings as text.\n"
                "Do NOT do more than 3 searches. After 3 searches, synthesize and respond.\n"
                "Return a concise step-by-step guide with exact URL patterns and button names."
            ),
            allowed_skills=("web_search",),
            preferred_model="gpt-5-mini",
            is_builtin=True,
        )

        try:
            from lazyclaw.teams.runner import run_specialist

            research_result = await run_specialist(
                user_id=user_id,
                specialist=_recon_specialist,
                task=research_task,
                registry=self._registry,
                eco_router=self._eco_router,
                permission_checker=self._permission_checker,
                callback=self._callback,
            )

            if research_result.success and research_result.result:
                # Save to site_memory for future visits
                try:
                    await remember(
                        self._config, user_id,
                        f"https://{domain}/",
                        memory_type="site_research",
                        title=f"How to: {action_hint[:80]}",
                        content={"workflow": research_result.result[:3000]},
                    )
                    logger.info("Saved site research for %s", domain)
                except Exception as e:
                    logger.warning("Failed to save site research: %s", e)

                return research_result.result

        except Exception as e:
            logger.warning("Site research failed for %s: %s", domain, e)

        return ""

    def _extract_domain(self, instruction: str) -> str:
        """Extract target domain from instruction text."""
        lower = instruction.lower()

        # Check known domain shortcuts
        for hint, domain in self._DOMAIN_HINTS.items():
            if hint in lower:
                return domain

        # Check for URLs in instruction
        import re
        url_match = re.search(r'https?://([^/\s]+)', instruction)
        if url_match:
            return url_match.group(1)

        # Check for domain-like patterns
        domain_match = re.search(r'([a-z0-9-]+\.[a-z]{2,})', lower)
        if domain_match:
            return domain_match.group(1)

        return ""
