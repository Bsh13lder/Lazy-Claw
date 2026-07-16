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
            logger.debug(
                "[dispatch] invalid LAZYCLAW_DISPATCH_CONCURRENCY=%r — using default",
                raw,
            )
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

        logger.debug(
            "[dispatch] sync dispatch() invoked: %d subagent(s), user=%s, concurrency_cap=%d",
            len(configs), user_id[:8] if user_id else "", cap,
        )
        if len(configs) > cap:
            logger.info(
                "dispatch: %d tasks, capping concurrency at %d",
                len(configs), cap,
            )
        tasks = [_run_with_cap(cfg) for cfg in configs]
        results = list(await asyncio.gather(*tasks))
        succeeded = sum(1 for r in results if r.success)
        logger.debug(
            "[dispatch] sync dispatch() settled: %d/%d succeeded",
            succeeded, len(results),
        )
        return results

    async def submit_async(
        self,
        configs: list[SubagentConfig],
        user_id: str,
        on_register=None,
        on_settle=None,
        fanout_group_id: str | None = None,
    ) -> list[str]:
        """Fire-and-forget submit. Returns task IDs immediately.

        Each subagent runs in its own asyncio task, registers with TeamLead
        for live activity-panel visibility, and publishes a terminal
        ``background_done`` / ``background_failed`` event to
        :mod:`lazyclaw.runtime.task_event_bus` when finished — same channel
        the WebSocket and Telegram pumps already listen on.

        The parent agent is freed immediately, so the user can keep chatting
        while the subagents run in the "background" lane.

        RC2 consolidation hooks (all optional; legacy behaviour when unset):

        * ``on_register(task_ids)`` — called ONCE, synchronously, BEFORE any
          subagent is spawned (so a fan-out group can be registered with the
          full task-id set before a sibling can settle — race-free).
        * ``on_settle(task_id, SubagentResult)`` — called as each subagent
          settles (may be sync or async). The dispatch_subagents skill wires
          this to ``TaskRunner.record_subagent_result`` so the last sibling
          triggers ONE consolidation turn.
        * ``fanout_group_id`` — when set, tagged onto the terminal bus event
          (with ``source="brain"``) so the chat WS pump drops the per-subagent
          side-note: the consolidator owns delivery, no double-render.
        """
        if not configs:
            return []
        logger.debug(
            "[dispatch] background submit_async() invoked: %d subagent(s), "
            "agent_types=%s, user=%s",
            len(configs),
            [c.agent_type.value for c in configs],
            user_id[:8] if user_id else "",
        )
        task_ids: list[str] = [
            f"subagent-{uuid.uuid4().hex[:8]}" for _ in configs
        ]
        # Register the fan-out group BEFORE spawning so its pending set is
        # populated before any subagent can settle and call on_settle.
        if on_register is not None:
            try:
                on_register(list(task_ids))
            except Exception:
                logger.debug(
                    "submit_async on_register hook failed for %d task(s), "
                    "fanout_group_id=%s",
                    len(task_ids), fanout_group_id, exc_info=True,
                )
        for cfg, task_id in zip(configs, task_ids):
            bg = asyncio.create_task(
                self._run_and_publish(
                    cfg, user_id, task_id,
                    on_settle=on_settle,
                    fanout_group_id=fanout_group_id,
                ),
                name=f"subagent-{cfg.agent_type.value}-{task_id[-8:]}",
            )
            _BACKGROUND_SUBAGENTS.add(bg)
            bg.add_done_callback(_BACKGROUND_SUBAGENTS.discard)
        logger.info(
            "submit_async: spawned %d background subagents for user %s "
            "(consolidating=%s)",
            len(task_ids), user_id, bool(fanout_group_id),
        )
        return task_ids

    async def _run_and_publish(
        self,
        cfg: SubagentConfig,
        user_id: str,
        task_id: str,
        on_settle=None,
        fanout_group_id: str | None = None,
    ) -> None:
        """Run one background subagent and publish its terminal event."""
        import inspect as _inspect
        from lazyclaw.runtime import task_event_bus

        # This runs in a DETACHED task spawned by submit_async — the parent
        # foreground turn already returned and ran its browser_turn_scope.finally,
        # so it no longer owns the live-Brave lock release. Re-enter the scope
        # here so this subagent gets its OWN holder (its task identity differs
        # from the inherited holder's .task) and releases the per-user lock in
        # its own finally when it finishes. Without this, the lock acquired on
        # the subagent's first browser tool call (e.g. an upwork_* read) leaks
        # for the process lifetime, and it also makes multiple dispatched
        # subagents correctly serialize on the per-user lock. Lazy import to
        # avoid a circular import.
        from lazyclaw.runtime.browser_turn_lock import (
            BACKGROUND_ROLE,
            browser_turn_scope,
        )

        # Subagents are background work → BACKGROUND browser lane, so they
        # never block the user's VISIBLE foreground tab/lock. See ADR-0005.
        async with browser_turn_scope(BACKGROUND_ROLE):
            result = await self._run_subagent(
                cfg, user_id, task_id_override=task_id,
            )

        kind = "background_done" if result.success else "background_failed"
        logger.debug(
            "[dispatch] background subagent settled: type=%s task=%s kind=%s "
            "result_len=%d duration_ms=%d",
            cfg.agent_type.value, task_id, kind,
            len(result.result or ""), result.duration_ms,
        )
        try:
            task_event_bus.publish(task_event_bus.TaskEvent(
                user_id=user_id,
                kind=kind,
                task_id=task_id,
                name=f"{cfg.agent_type.value} subagent",
                result=(result.result or "")[:4000] if result.success else None,
                error=result.error if not result.success else None,
                duration_ms=result.duration_ms,
                # RC2: tag consolidating fan-outs so the chat WS pump drops
                # the per-subagent side-note (consolidator owns delivery).
                source="brain" if fanout_group_id else None,
                fanout_group_id=fanout_group_id,
            ))
        except Exception:
            logger.debug(
                "task_event_bus publish failed for %s (kind=%s)",
                task_id, kind, exc_info=True,
            )

        # RC2: feed the settled result into the fan-out group so the last
        # sibling triggers ONE consolidation turn. Fired AFTER the bus event
        # so the (dropped) side-note can never race ahead of consolidation.
        if on_settle is not None:
            try:
                _maybe = on_settle(task_id, result)
                if _inspect.isawaitable(_maybe):
                    await _maybe
            except Exception:
                logger.debug(
                    "submit_async on_settle hook failed for %s (fanout_group_id=%s)",
                    task_id, fanout_group_id, exc_info=True,
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
        from lazyclaw.runtime.callbacks import (
            CancellationToken, SilentSubagentCallback,
        )

        # Mark context as subagent — blocks nested dispatch_subagents calls
        token = _IS_SUBAGENT.set(True)
        start = time.monotonic()

        task_id = task_id_override or f"subagent-{uuid.uuid4().hex[:8]}"
        # Per-subagent cancel token so the Activity panel "cancel" button
        # can stop a single subagent without taking down the rest.
        sub_cancel = CancellationToken()

        logger.debug(
            "[dispatch] _run_subagent start: type=%s task=%s user=%s timeout=%ds",
            cfg.agent_type.value, task_id,
            user_id[:8] if user_id else "", cfg.timeout,
        )

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
                    cancel_token=sub_cancel,
                    user_id=user_id,
                )
            except Exception:
                logger.debug(
                    "team_lead.register failed for %s (agent_type=%s, user=%s)",
                    task_id, cfg.agent_type.value,
                    user_id[:8] if user_id else "", exc_info=True,
                )

        # Background subagents are silent on the chat WS by design — events
        # only drive TeamLead, which fans out to the Activity panel via
        # task_event_bus. The brain learns the outcome via the consolidated
        # background_done side-note on its next TAOR iteration, NOT through
        # passthrough of subagent steps.
        wrapped_callback = SilentSubagentCallback(
            team_lead=self._team_lead,
            task_id=task_id,
            cancel_token=sub_cancel,
        ) if self._team_lead is not None else None

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
                    cancel_token=sub_cancel,
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
                        "team_lead.complete/fail failed for %s (agent_type=%s)",
                        task_id, cfg.agent_type.value, exc_info=True,
                    )

            logger.debug(
                "[dispatch] _run_subagent done: type=%s task=%s success=%s "
                "result_len=%d duration_ms=%d",
                cfg.agent_type.value, task_id, result.success,
                len(result.result or ""), duration_ms,
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
                "[dispatch] Subagent %s (task=%s) timed out after %ds "
                "(task_len=%d chars)",
                cfg.agent_type.value, task_id, cfg.timeout, len(cfg.task),
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
                "[dispatch] Subagent %s (task=%s) failed: %s: %s "
                "(task_len=%d chars)",
                cfg.agent_type.value, task_id, type(exc).__name__, exc,
                len(cfg.task),
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
            # Union in mcp-scraper pool tool names. Pool registers tools
            # under canonical `mcp_scraper_<tool>` (one entry per tool,
            # not per shard) — so name-prefix match is exact and gives
            # the EXPLORE worker direct access to extract_entities /
            # crawl_url without falling back to browser.
            try:
                for t in self._registry.list_mcp_tools():
                    func = t.get("function", {})
                    name = func.get("name", "")
                    if name.startswith("mcp_scraper_"):
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
