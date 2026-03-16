"""Team lead — analyzes requests, delegates to specialists, merges results.

The team lead is the brain of the multi-agent system. It:
1. Analyzes whether a message needs team mode or direct response
2. Breaks complex tasks into sub-tasks for specialists
3. Dispatches specialists in parallel via the executor
4. Merges results (with critic review when 2+ specialists used)
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.teams.conversation import store_message
from lazyclaw.teams.executor import TeamTask, execute_team
from lazyclaw.teams.runner import SpecialistResult
from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)

# Prompt for the team lead's analysis step
_ANALYZE_PROMPT = """\
You are a team lead AI. Analyze the user's message and decide whether to handle it \
directly (simple) or delegate to specialist agents (team).

Available specialists:
{specialists_desc}

Rules:
- Simple questions (greetings, time, single facts, short answers) → "simple"
- Tasks requiring tools, multiple steps, or multiple domains → "team"
- If a task clearly maps to one specialist's expertise → assign to that specialist
- If a task spans multiple domains → assign sub-tasks to relevant specialists
- Never assign more than 4 specialists for one request

Respond with ONLY valid JSON (no markdown, no explanation):

For simple: {{"mode": "simple"}}
For team: {{"mode": "team", "tasks": [{{"specialist": "name", "instruction": "what to do"}}, ...]}}
"""

# Prompt for merging specialist results
_MERGE_PROMPT = """\
You are synthesizing results from specialist agents into one coherent response \
for the user. The user's original request was:

"{original_message}"

Specialist results:
{results_text}

{critic_instruction}

Produce a clear, well-organized response that:
1. Addresses the user's original request completely
2. Integrates findings from all specialists naturally
3. Does not mention specialists, teams, or internal processes — respond as one agent
"""

_CRITIC_INSTRUCTION = (
    "ALSO: Review the combined results for accuracy, contradictions, gaps, "
    "or unsupported claims. If you find issues, note them briefly at the end "
    "under a '---' separator. If everything looks solid, omit this section."
)


def _build_specialists_description(specialists: list[SpecialistConfig]) -> str:
    """Format specialist list for the analysis prompt."""
    lines = []
    for s in specialists:
        skills = ", ".join(s.allowed_skills)
        lines.append(f"- {s.name} ({s.display_name}): skills=[{skills}]")
    return "\n".join(lines)


def _format_results(results: list[SpecialistResult]) -> str:
    """Format specialist results for the merge prompt."""
    parts = []
    for r in results:
        status = "completed" if r.success else f"FAILED: {r.error}"
        tools = ", ".join(r.tools_used) if r.tools_used else "none"
        parts.append(
            f"### {r.agent_name} ({status})\n"
            f"Tools used: {tools}\n"
            f"Duration: {r.duration_ms}ms\n\n"
            f"{r.result}"
        )
    return "\n\n---\n\n".join(parts)


def _parse_analysis(content: str) -> dict | None:
    """Parse the team lead's JSON analysis response."""
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "mode" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Try to find JSON in the response
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return None


import re as _re

# Fast pre-filter: patterns that suggest action (need team analysis)
_ACTION_KEYWORDS = _re.compile(
    r"\b(search|browse|find|create|write|run|schedule|compare|analyze|"
    r"build|deploy|generate|compute|calculate|check|review|debug|fix|"
    r"refactor|test|monitor|scrub|scan)\b",
    _re.IGNORECASE,
)


def _is_obviously_simple(message: str) -> bool:
    """Return True if the message is clearly too simple for team delegation.

    Avoids a full LLM analysis call for greetings, thanks, short questions.
    """
    if len(message) > 80:
        return False
    if _ACTION_KEYWORDS.search(message):
        return False
    # Short messages without action keywords → simple
    return True


class TeamLead:
    """Orchestrates multi-agent team execution."""

    def __init__(self, config: Config, eco_router: EcoRouter) -> None:
        self._config = config
        self._eco_router = eco_router

    async def process(
        self,
        user_id: str,
        message: str,
        settings: dict,
        specialists: list[SpecialistConfig],
        registry: SkillRegistry,
        permission_checker,
    ) -> str | None:
        """Analyze message and optionally delegate to specialists.

        Returns:
            str — merged team response (team mode activated)
            None — message is simple, caller should use normal agent
        """
        mode = settings.get("mode", "auto")

        # If mode is "always", skip analysis and force team mode
        if mode == "always":
            return await self._force_team(
                user_id, message, settings, specialists, registry, permission_checker
            )

        # Fast pre-filter: skip LLM analysis for obviously simple messages
        if _is_obviously_simple(message):
            logger.debug("Team lead: skipped analysis (obviously simple)")
            return None

        # Analyze complexity
        analysis = await self._analyze(user_id, message, specialists)
        if analysis is None or analysis.get("mode") == "simple":
            logger.debug("Team lead: simple mode for message")
            return None

        # Team mode — delegate and merge
        tasks_data = analysis.get("tasks", [])
        if not tasks_data:
            return None

        return await self._execute_team(
            user_id, message, tasks_data, settings, specialists,
            registry, permission_checker,
        )

    async def _analyze(
        self,
        user_id: str,
        message: str,
        specialists: list[SpecialistConfig],
    ) -> dict | None:
        """Ask LLM to classify the message as simple or team."""
        desc = _build_specialists_description(specialists)
        prompt = _ANALYZE_PROMPT.format(specialists_desc=desc)

        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=message),
        ]

        response = await self._eco_router.chat(
            messages, user_id=user_id, model=self._config.default_model,
        )
        return _parse_analysis(response.content or "")

    async def _force_team(
        self,
        user_id: str,
        message: str,
        settings: dict,
        specialists: list[SpecialistConfig],
        registry: SkillRegistry,
        permission_checker,
    ) -> str | None:
        """Force team mode: always delegate, even for simple messages."""
        analysis = await self._analyze(user_id, message, specialists)

        if analysis and analysis.get("tasks"):
            tasks_data = analysis["tasks"]
        else:
            # Default: assign to research specialist for generic tasks
            tasks_data = [{"specialist": "research_specialist", "instruction": message}]

        return await self._execute_team(
            user_id, message, tasks_data, settings, specialists,
            registry, permission_checker,
        )

    async def _execute_team(
        self,
        user_id: str,
        message: str,
        tasks_data: list[dict],
        settings: dict,
        specialists: list[SpecialistConfig],
        registry: SkillRegistry,
        permission_checker,
    ) -> str:
        """Build tasks, execute in parallel, merge results."""
        team_session_id = str(uuid4())

        # Map specialist names to configs
        spec_map = {s.name: s for s in specialists}

        # Build TeamTask list
        team_tasks: list[TeamTask] = []
        for td in tasks_data:
            spec_name = td.get("specialist", "")
            instruction = td.get("instruction", message)

            spec = spec_map.get(spec_name)
            if not spec:
                logger.warning("Unknown specialist '%s', skipping", spec_name)
                continue

            team_tasks.append(TeamTask(specialist=spec, instruction=instruction))

        if not team_tasks:
            return None

        # Store team lead instructions
        for tt in team_tasks:
            await store_message(
                self._config, user_id, team_session_id,
                from_agent="team_lead",
                to_agent=tt.specialist.name,
                message_type="instruction",
                content=tt.instruction,
            )

        # Execute in parallel
        max_parallel = settings.get("max_parallel", 3)
        timeout = settings.get("specialist_timeout", 120)

        results = await execute_team(
            tasks=team_tasks,
            user_id=user_id,
            registry=registry,
            eco_router=self._eco_router,
            permission_checker=permission_checker,
            max_parallel=max_parallel,
            timeout=timeout,
        )

        # Store specialist results
        for r in results:
            content = r.result if r.success else f"ERROR: {r.error}"
            await store_message(
                self._config, user_id, team_session_id,
                from_agent=r.agent_name,
                to_agent="team_lead",
                message_type="result",
                content=content,
            )

        # Filter to successful results
        successful = [r for r in results if r.success and r.result]

        if not successful:
            error_msgs = [f"{r.agent_name}: {r.error}" for r in results if not r.success]
            return "All specialists encountered errors:\n" + "\n".join(error_msgs)

        # Merge results (with critic if 2+ specialists)
        merged = await self._merge_results(
            user_id, message, successful, settings, team_session_id
        )

        return merged

    async def _merge_results(
        self,
        user_id: str,
        original_message: str,
        results: list[SpecialistResult],
        settings: dict,
        team_session_id: str,
    ) -> str:
        """Merge specialist results via LLM, with optional critic review."""
        critic_mode = settings.get("critic_mode", "auto")

        # Determine if critic should be active
        use_critic = (
            critic_mode == "always"
            or (critic_mode == "auto" and len(results) >= 2)
        )

        critic_text = _CRITIC_INSTRUCTION if use_critic else ""
        results_text = _format_results(results)

        prompt = _MERGE_PROMPT.format(
            original_message=original_message,
            results_text=results_text,
            critic_instruction=critic_text,
        )

        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(
                role="user",
                content="Synthesize the specialist results into a single response.",
            ),
        ]

        response = await self._eco_router.chat(
            messages, user_id=user_id, model=self._config.default_model,
        )
        merged_content = response.content or ""

        # Store merge/critic result
        msg_type = "critique" if use_critic else "result"
        await store_message(
            self._config, user_id, team_session_id,
            from_agent="team_lead",
            to_agent="user",
            message_type=msg_type,
            content=merged_content,
        )

        return merged_content
