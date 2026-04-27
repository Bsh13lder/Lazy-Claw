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
import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.runtime.team_lead import TeamLead
    from lazyclaw.skills.registry import SkillRegistry


# Module-level GC pin for fire-and-forget background subagent tasks.
# Without holding a strong reference, asyncio may GC the task mid-flight.
_BACKGROUND_SUBAGENTS: set[asyncio.Task] = set()

logger = logging.getLogger(__name__)

# ── Single-depth enforcement ────────────────────────────────────────────
# Set to True inside _run_subagent coroutines so nested dispatch is blocked.
_IS_SUBAGENT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "lazyclaw_is_subagent", default=False,
)

# ── Concurrency cap ──────────────────────────────────────────────────────
# MiniMax Token Plan and most paid LLM endpoints rate-limit per-account
# concurrency aggressively. Firing 12 subagents at once via asyncio.gather
# guarantees a 429 burst. Cap concurrent in-flight subagents and let the
# rest queue up. Override with LAZYCLAW_DISPATCH_CONCURRENCY.
def _dispatch_concurrency() -> int:
    raw = os.environ.get("LAZYCLAW_DISPATCH_CONCURRENCY")
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return n
        except ValueError:
            pass
    return 4


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
    "You are a read-only research agent. You gather information and return a "
    "structured summary. You MUST NOT modify state — no writes, sends, creates, "
    "or deletes.\n\n"
    "TOOL PRIORITY (read top-to-bottom, stop at first match):\n"
    "1. `web_search` for ANY general lookup. It's scraper-backed (free, "
    "JS-rendered Google) — no quota, no API key. Use Google search "
    "operators to make it precise:\n"
    "     - `site:domain.com query` — restrict to one site\n"
    "     - `\"exact phrase\"` — match phrase verbatim\n"
    "     - `intitle:word` / `inurl:word` — narrow by URL/title\n"
    "   Examples that DON'T need anything else:\n"
    "     - Find IG handle of a business → "
    "`web_search 'site:instagram.com <business name> <city>'` "
    "(handle is in the URL — read it from the result, no need to open the page).\n"
    "     - Address/phone of a small business → "
    "`web_search '<business name> <city> phone'` "
    "(rich snippets often contain the answer directly).\n"
    "2. Once `web_search` gives you a URL and you need a field on the page:\n"
    "   - Email / phone / socials → call the scraper tool that ends in "
    "`_extract_entities(url, entity_types=[\"email\", \"phone\"])`. JS-rendered, "
    "returns structured dict. Use this BEFORE opening browser.\n"
    "   - Full page text in markdown → tool ending in `_crawl_url(url)`.\n"
    "   - Multi-page same-site → tool ending in `_deep_crawl_site(url, max_depth=2)`.\n"
    "   Tool names are auto-prefixed with `mcp_<uuid>_…`; pick by suffix.\n"
    "   Skip scraper for instagram.com / facebook.com / linkedin.com — same "
    "anti-bot wall as browser.\n"
    "3. `browser` is the LAST RESORT, used only when:\n"
    "   - web_search + scraper both came back empty, AND\n"
    "   - you actually have to read interactive DOM (login, click-through, search-as-you-type).\n"
    "   Open ONE page, extract, move on. Never loop on browser — if 2 opens "
    "haven't yielded the answer, switch back to web_search with a new query.\n"
    "   NEVER open instagram.com / facebook.com / linkedin.com URLs — they block "
    "bots and serve a login wall.\n"
    "4. `read_file` / `list_directory` for local files. `recall_memories` for "
    "user preferences and prior context.\n\n"
    "BUDGET — STOP EARLY:\n"
    "- Hard cap: max 5 tool calls per task. Beyond that, returns diminish.\n"
    "- If web_search + 1 scraper call haven't surfaced the answer, the answer "
    "probably isn't publicly indexed. Stop and return \"Not found\" with a "
    "one-line note on what you tried — DO NOT keep varying queries hoping the "
    "next one works.\n"
    "- Small-business contact data (emails especially) is often gated behind "
    "login or hidden by anti-scraping. \"Not found\" after a real attempt is a "
    "valid, useful answer.\n\n"
    "Cite sources (URLs) for every fact you return. If a tool returns nothing "
    "useful, say so — never invent data."
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
        team_lead: TeamLead | None = None,
        callback: AgentCallback | None = None,
    ) -> None:
        self._config = config
        self._eco_router = eco_router
        self._registry = registry
        self._permission_checker = permission_checker
        self._team_lead = team_lead
        self._callback = callback

    async def dispatch(
        self,
        configs: list[SubagentConfig],
        user_id: str,
    ) -> list[SubagentResult]:
        """Blocking dispatch — returns results in input order.

        Kept for callers that need an inline merged answer. The skill-level
        ``dispatch_subagents`` tool now uses :meth:`submit_async` so the
        parent agent does not block on long fan-outs. Concurrency is capped
        (default 4) to avoid 429 bursts on per-account LLM rate limits.
        """
        if not configs:
            return []
        cap = _dispatch_concurrency()
        sem = asyncio.Semaphore(cap)

        async def _run_with_cap(cfg: SubagentConfig) -> SubagentResult:
            async with sem:
                return await self._run_subagent(cfg, user_id)

        if len(configs) > cap:
            logger.info(
                "dispatch: %d tasks, capping concurrency at %d",
                len(configs), cap,
            )
        tasks = [_run_with_cap(cfg) for cfg in configs]
        return list(await asyncio.gather(*tasks))

    async def submit_async(
        self,
        configs: list[SubagentConfig],
        user_id: str,
    ) -> list[str]:
        """Fire-and-forget submit. Returns task IDs immediately.

        Each subagent runs in its own asyncio task, registers with TeamLead
        for live activity-panel visibility, and publishes a terminal
        ``background_done`` / ``background_failed`` event to
        :mod:`lazyclaw.runtime.task_event_bus` when finished — same channel
        the WebSocket and Telegram pumps already listen on.

        The parent agent is freed immediately, so the user can keep chatting
        while the subagents run in the "background" lane.
        """
        if not configs:
            return []
        task_ids: list[str] = []
        for cfg in configs:
            task_id = f"subagent-{uuid.uuid4().hex[:8]}"
            task_ids.append(task_id)
            bg = asyncio.create_task(
                self._run_and_publish(cfg, user_id, task_id),
                name=f"subagent-{cfg.agent_type.value}-{task_id[-8:]}",
            )
            _BACKGROUND_SUBAGENTS.add(bg)
            bg.add_done_callback(_BACKGROUND_SUBAGENTS.discard)
        logger.info(
            "submit_async: spawned %d background subagents for user %s",
            len(task_ids), user_id,
        )
        return task_ids

    async def _run_and_publish(
        self,
        cfg: SubagentConfig,
        user_id: str,
        task_id: str,
    ) -> None:
        """Run one background subagent and publish its terminal event."""
        from lazyclaw.runtime import task_event_bus

        result = await self._run_subagent(cfg, user_id, task_id_override=task_id)

        kind = "background_done" if result.success else "background_failed"
        try:
            task_event_bus.publish(task_event_bus.TaskEvent(
                user_id=user_id,
                kind=kind,
                task_id=task_id,
                name=f"{cfg.agent_type.value} subagent",
                result=(result.result or "")[:4000] if result.success else None,
                error=result.error if not result.success else None,
                duration_ms=result.duration_ms,
            ))
        except Exception:
            logger.debug(
                "task_event_bus publish failed for %s", task_id, exc_info=True,
            )

    async def _run_subagent(
        self,
        cfg: SubagentConfig,
        user_id: str,
        task_id_override: str | None = None,
    ) -> SubagentResult:
        """Run a single subagent. Sets _IS_SUBAGENT to block recursive dispatch.

        When ``task_id_override`` is provided (the async-submit path), it's
        reused for TeamLead bookkeeping so the panel and the terminal event
        share the same ID.
        """
        from lazyclaw.teams.runner import run_specialist
        from lazyclaw.runtime.callbacks import StepTrackingCallback

        # Mark context as subagent — blocks nested dispatch_subagents calls
        token = _IS_SUBAGENT.set(True)
        start = time.monotonic()

        task_id = task_id_override or f"subagent-{uuid.uuid4().hex[:8]}"

        # Register with TeamLead so the Activity panel shows the subagent
        # alongside specialists and background tasks.
        if self._team_lead is not None:
            try:
                self._team_lead.register(
                    task_id=task_id,
                    name=f"{cfg.agent_type.value}_subagent",
                    description=cfg.task[:80],
                    lane="subagent",
                    instruction_full=cfg.task,
                    user_id=user_id,
                )
            except Exception:
                logger.debug(
                    "team_lead.register failed for %s", task_id, exc_info=True,
                )

        # Wrap callback so per-tool events drive update_step, surfacing the
        # subagent's current tool in the panel just like specialists do.
        wrapped_callback = self._callback
        if self._team_lead is not None and self._callback is not None:
            wrapped_callback = StepTrackingCallback(
                inner=self._callback,
                team_lead=self._team_lead,
                task_id=task_id,
            )

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
                    callback=wrapped_callback,
                ),
                timeout=cfg.timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            if self._team_lead is not None:
                try:
                    if result.success:
                        self._team_lead.complete(
                            task_id,
                            result_preview=(result.result or "")[:100],
                            result_full=result.result or "",
                        )
                    else:
                        self._team_lead.fail(task_id, error=result.error or "")
                except Exception:
                    logger.debug(
                        "team_lead.complete/fail failed for %s",
                        task_id, exc_info=True,
                    )

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
            err = f"Timed out after {cfg.timeout}s"
            logger.warning(
                "Subagent %s timed out after %ds: %.60s",
                cfg.agent_type.value, cfg.timeout, cfg.task,
            )
            if self._team_lead is not None:
                try:
                    self._team_lead.fail(task_id, error=err)
                except Exception:
                    logger.debug(
                        "team_lead.fail failed for %s", task_id, exc_info=True,
                    )
            return SubagentResult(
                agent_type=cfg.agent_type,
                task=cfg.task,
                result="",
                success=False,
                tokens_used=0,
                duration_ms=duration_ms,
                error=err,
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "Subagent %s failed: %s — %.60s",
                cfg.agent_type.value, exc, cfg.task,
            )
            if self._team_lead is not None:
                try:
                    self._team_lead.fail(task_id, error=str(exc)[:200])
                except Exception:
                    logger.debug(
                        "team_lead.fail failed for %s", task_id, exc_info=True,
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
            base_allowed = (
                set(cfg.tool_names) if cfg.tool_names else set(_EXPLORE_TOOLS)
            )
            # Union in mcp-scraper tool names. Tool ids are dynamic
            # (`mcp_<server_uuid>_<toolname>`) so we can't hard-code them
            # in `_EXPLORE_TOOLS`. Match by description containing the
            # server name (`mcp-scraper`). Without this, EXPLORE workers
            # cannot reach `extract_entities` / `crawl_url` and fall back
            # to `browser` even when the brain told them to use scraper.
            try:
                for t in self._registry.list_mcp_tools():
                    func = t.get("function", {})
                    name = func.get("name", "")
                    desc = func.get("description", "").lower()
                    if "mcp-scraper" in desc:
                        base_allowed.add(name)
            except Exception:
                logger.debug(
                    "Couldn't enumerate MCP tools for EXPLORE whitelist",
                    exc_info=True,
                )
            allowed = tuple(sorted(base_allowed))
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
