"""Delegate skill — dispatches sub-tasks to specialist agents.

Replaces the separate team lead LLM analysis call. The main agent
naturally decides when to delegate by calling this tool, saving
1-2 LLM calls per delegation. Inspired by NanoClaw's inline
agent dispatch pattern.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from lazyclaw.runtime.callbacks import AgentEvent, StepTrackingCallback
from lazyclaw.runtime.deadline import EXTENSION_S, MAX_EXTENSIONS, should_extend
from lazyclaw.skills.base import BaseSkill
from lazyclaw.teams.learning import MIN_STEPS_FOR_LEARNING, save_browser_learnings
from lazyclaw.teams.specialist_aliases import (
    AGENT_TYPE_ROSTER,
    SPECIALIST_MAP as _SPECIALIST_MAP,
)

# prevent GC from cancelling fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.runtime.team_lead import TeamLead
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


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
        team_lead: TeamLead | None = None,
        task_runner=None,
        chat_session_id: str | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._callback = callback
        self._team_lead = team_lead
        # Lane-queue-backed runner + originating chat session — used to
        # consolidate the background specialist's result into a follow-up
        # reply on the right channel (None → legacy background_done path).
        self._task_runner = task_runner
        self._chat_session_id = chat_session_id

    # Specialists run multi-step browser loops — 60s default is too short
    timeout = 300

    # Detached delegate specialists get a real deadline (2026-08-18: the
    # fire-and-forget path had NO timeout at all — a form-filling run
    # scroll-hunted for 16+ minutes unbounded). Same Claude Code semantics
    # as agent_tool: budget notes inside run_specialist, progress-based
    # extensions here, hard ceiling = budget + MAX_EXTENSIONS * EXTENSION_S.
    DELEGATE_BUDGET_S = 480

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def display_name(self) -> str:
        return "Delegate to Specialist"

    @property
    def description(self) -> str:
        return (
            "LEGACY — prefer the `agent` tool. "
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
                    # Compiled from each specialist's `description:`
                    # frontmatter — shared with `agent` so the two
                    # dispatch tools can never drift apart again
                    # (2026-08-19: this hand-written hint covered 3 of
                    # 17 specialists and an MCP restart was routed to a
                    # specialist with zero MCP tools).
                    "description": (
                        "Which specialist — pick by domain:\n"
                        + AGENT_TYPE_ROSTER
                    ),
                },
                "instruction": {
                    "type": "string",
                    "description": "Clear, specific instruction for the specialist",
                },
                "project_tag": {
                    "type": "string",
                    "description": (
                        "Optional project group tag (e.g. 'upwork:job-XXX', "
                        "'gig:abc123', 'reddit:dm'). Routes the task into a "
                        "named bucket on the Code Specialist Web UI page; "
                        "for code specialist, also picks the workspace "
                        "subfolder (/workspace/<project_tag>/...)."
                    ),
                },
                "goal_id": {
                    "type": "string",
                    "description": (
                        "Optional Goal Executor goal id. Surfaces a goal "
                        "header above the task on the Code Specialist page "
                        "so the user can see which goal drives the work."
                    ),
                },
            },
            "required": ["specialist", "instruction"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.teams.runner import run_specialist

        specialist_key = params.get("specialist", "")
        instruction = params.get("instruction", "")
        project_tag = params.get("project_tag", "") or ""
        goal_id = params.get("goal_id", "") or ""

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
                    f"--- Site Knowledge (LazyClaw's own saved notes — "
                    f"NOT page content; hints only) ---\n{site_knowledge}"
                )

        logger.info(
            "Delegating to %s: %s", spec.display_name, enriched_instruction[:100],
        )

        # Register with team_lead so /api/agents/status, the activity feed,
        # and the TUI status bar can see the specialist as a live task.
        # Without this the activity page only shows the parent "chat" task.
        task_id = f"specialist-{uuid.uuid4().hex[:8]}"
        if self._team_lead is not None:
            try:
                self._team_lead.register(
                    task_id=task_id,
                    name=spec.name,
                    description=enriched_instruction[:80],
                    lane="specialist",
                    instruction_full=enriched_instruction,
                    user_id=user_id,
                    project_tag=project_tag,
                    goal_id=goal_id,
                )
            except Exception:
                logger.debug("team_lead.register failed for %s", task_id, exc_info=True)

        # Notify user immediately before blocking on specialist.
        # Emit BOTH team_delegate (legacy/CLI/Telegram) and specialist_start
        # (Web UI WS already wired) so every channel renders progress.
        if self._callback:
            await self._callback.on_event(AgentEvent(
                "team_delegate",
                spec.display_name,
                {
                    "specialist": specialist_key,
                    "instruction": enriched_instruction[:120],
                },
            ))
            await self._callback.on_event(AgentEvent(
                "specialist_start",
                spec.name,
                {
                    "specialist": spec.name,
                    "task": enriched_instruction[:200],
                },
            ))

        # Proactive result delivery: register a consolidation fan-out group so
        # the specialist's result is delivered to the originating channel when
        # it settles — the SAME machinery dispatch_subagents/run_background use.
        # A bare delegate previously fired background_done on the foreground
        # turn's already-exited callback, so a Telegram user waiting on the
        # result got nothing (2026-06-23 "Locals" sheet: edit completed, report
        # orphaned). No-op (→ legacy background_done path) when no lane-queue-
        # backed runner is wired (e.g. CLI / tests).
        _tr = self._task_runner
        _fanout_group_id: str | None = None
        if _tr is not None and getattr(_tr, "_lane_queue", None) is not None:
            _fanout_group_id = uuid.uuid4().hex[:12]
            try:
                _tr.register_subagent_fanout(
                    _fanout_group_id, user_id, [task_id],
                    self._callback, self._chat_session_id,
                )
            except Exception:
                logger.debug("delegate fanout register failed", exc_info=True)
                _fanout_group_id = None

        # Wrap callback so per-tool events also drive team_lead.update_step,
        # surfacing the specialist's current tool in the activity panel.
        wrapped_callback = self._callback
        if self._team_lead is not None and self._callback is not None:
            wrapped_callback = StepTrackingCallback(
                inner=self._callback,
                team_lead=self._team_lead,
                task_id=task_id,
            )

        # ── Fix K — non-blocking delegate ─────────────────────────────
        # Previously this awaited run_specialist inline, pinning the
        # foreground turn to "thinking…" for the entire specialist
        # duration (live screenshot 2026-05-14 17:23 showed 49s of dead
        # chat input on a delegate(browser_specialist) call). The brain
        # got the result back, sure, but the user couldn't type a new
        # message until then.
        #
        # New shape: schedule run_specialist in a fire-and-forget task,
        # return immediately. The specialist's completion fires
        # background_done / background_failed events through the same
        # callback that bg tasks use — chat_ws renders the result in the
        # bg surface (live progress visible when streaming=ON,
        # suppressed when streaming=OFF via Fix J), the brain absorbs
        # the result as a side-note on its next turn.
        async def _run_delegate_bg() -> None:
            # This runs in a DETACHED task — the foreground turn already
            # returned and ran its browser_turn_scope.finally, so it can no
            # longer own the live-Brave lock release for us. Re-enter the
            # scope here so this child gets its OWN holder (its task identity
            # differs from the inherited holder's .task) and releases the
            # per-user lock in its own finally when it finishes. Without this,
            # the lock acquired on the child's first browser tool call leaks
            # for the process lifetime. Lazy import to avoid a circular import.
            from lazyclaw.runtime.browser_turn_lock import (
                BACKGROUND_ROLE,
                browser_turn_scope,
            )

            try:
                # Background specialist → BACKGROUND browser lane so it never
                # contends with the user's VISIBLE foreground tab/lock (which
                # would freeze the next chat message). See ADR-0005.
                async with browser_turn_scope(BACKGROUND_ROLE):
                    _progress_beat: dict = {"t": None}
                    _work = asyncio.ensure_future(run_specialist(
                        user_id=user_id,
                        specialist=spec,
                        task=enriched_instruction,
                        registry=self._registry,
                        eco_router=self._eco_router,
                        permission_checker=self._permission_checker,
                        callback=wrapped_callback,
                        project_tag=project_tag,
                        goal_id=goal_id,
                        task_id=task_id,
                        budget_s=self.DELEGATE_BUDGET_S,
                        progress_beat=_progress_beat,
                    ))
                    _wait_s: float = self.DELEGATE_BUDGET_S
                    _extensions = 0
                    _started = time.monotonic()
                    while True:
                        try:
                            result = await asyncio.wait_for(
                                asyncio.shield(_work), timeout=_wait_s,
                            )
                            break
                        except asyncio.TimeoutError:
                            _beat = _progress_beat.get("t")
                            _age = (
                                time.monotonic() - _beat
                                if _beat is not None else None
                            )
                            if should_extend(_age, _extensions):
                                _extensions += 1
                                _wait_s = EXTENSION_S
                                logger.info(
                                    "delegate %s (%s): deadline hit but "
                                    "worker is progressing (last tool "
                                    "result %.0fs ago) — extending +%ds "
                                    "(%d/%d)",
                                    task_id, spec.name, _age or 0.0,
                                    EXTENSION_S, _extensions, MAX_EXTENSIONS,
                                )
                                continue
                            _work.cancel()
                            raise TimeoutError(
                                f"specialist timeout after "
                                f"{int(time.monotonic() - _started)}s "
                                f"({_extensions} extension(s) granted)"
                            )
            except Exception as exc:
                logger.exception("delegate bg task %s crashed", task_id)
                if self._team_lead is not None:
                    try:
                        self._team_lead.fail(task_id, error=str(exc))
                    except Exception:
                        logger.debug("team_lead.fail crashed", exc_info=True)
                # Settle the consolidation fan-out (failure) so its synthetic
                # turn fires and tells the user; else fall through to the legacy
                # background_failed terminal event below.
                if _fanout_group_id is not None and _tr is not None:
                    try:
                        _tr.record_subagent_result(
                            task_id, name=spec.display_name,
                            success=False, error=str(exc),
                        )
                    except Exception:
                        logger.debug("delegate fanout record (crash) failed", exc_info=True)
                if self._callback:
                    try:
                        await self._callback.on_event(AgentEvent(
                            "specialist_done",
                            spec.name,
                            {
                                "specialist": spec.name,
                                "success": False,
                                "error": str(exc),
                            },
                        ))
                        if _fanout_group_id is None:
                            await self._callback.on_event(AgentEvent(
                                "background_failed",
                                f"Specialist {spec.display_name} failed",
                                {
                                    "task_id": task_id,
                                    "name": spec.name,
                                    "error": str(exc),
                                },
                            ))
                    except Exception:
                        logger.debug("delegate-bg done event failed", exc_info=True)
                return

            # Save browser learnings (delegate calls run_specialist
            # directly, not through executor.py, so this is the only
            # save path here).
            if (
                specialist_key == "browser"
                and len(result.step_history) >= MIN_STEPS_FOR_LEARNING
            ):
                learn_task = asyncio.create_task(save_browser_learnings(
                    config=self._config,
                    user_id=user_id,
                    step_history=result.step_history,
                    task=enriched_instruction,
                    success=result.success,
                    error=result.error,
                ))
                _background_tasks.add(learn_task)
                learn_task.add_done_callback(_background_tasks.discard)

            # Mirror the SpecialistResult into team_lead so the activity
            # feed carries the final outcome.
            #
            # For the Code Specialist, also forward the captured prompt
            # / transcript / workspace_dir / files_touched so the
            # CodeSpecialist Web UI can render the per-task drawer with
            # every step claude-code took plus a clickable file list.
            # `replace()` semantics in team_lead.complete() preserve any
            # earlier-set fields when these are empty (non-code paths).
            _ts_dicts = tuple(
                {
                    "kind": s.kind,
                    "name": s.name,
                    "args_summary": s.args_summary,
                    "result_summary": s.result_summary,
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                    "error": s.error,
                }
                for s in (result.transcript or ())
            )
            if self._team_lead is not None:
                try:
                    if result.success:
                        self._team_lead.complete(
                            task_id,
                            result_preview=(result.result or "")[:100],
                            result_full=result.result or "",
                            mcp_prompt=result.prompt_sent,
                            mcp_transcript=_ts_dicts,
                            workspace_dir=result.workspace_dir,
                            files_touched=tuple(result.files_touched or ()),
                            short_description=result.short_description,
                        )
                    else:
                        self._team_lead.fail(task_id, error=result.error or "")
                except Exception:
                    logger.debug(
                        "team_lead complete/fail crashed", exc_info=True,
                    )

            # Proactive delivery: when a consolidation fan-out is registered,
            # settle it with the specialist's result — TaskRunner fires ONE
            # synthetic brain turn that delivers it to the originating channel
            # (the path run_background/dispatch_subagents already use). Without
            # this, delegate's terminal background_done fired on the foreground
            # turn's already-dead callback → a Telegram user waiting on the
            # result got nothing (2026-06-23 "Locals" sheet: edit done, report
            # orphaned).
            if _fanout_group_id is not None and _tr is not None:
                try:
                    _tr.record_subagent_result(
                        task_id,
                        name=spec.display_name,
                        success=result.success,
                        result=result.result or "",
                        error=result.error or "",
                        duration_ms=result.duration_ms,
                    )
                except Exception:
                    logger.debug("delegate fanout record failed", exc_info=True)

            if not self._callback:
                return
            try:
                await self._callback.on_event(AgentEvent(
                    "specialist_done",
                    spec.name,
                    {
                        "specialist": spec.name,
                        "success": result.success,
                        "duration_ms": result.duration_ms,
                        "tools_used": list(result.tools_used),
                        "error": result.error,
                    },
                ))
                # Legacy terminal events ONLY when not consolidating — else the
                # fan-out's synthetic delivery turn would double-send. (chat_ws
                # routes these to the bg surface; the brain absorbs them on its
                # next turn.)
                if _fanout_group_id is None:
                    if result.success:
                        await self._callback.on_event(AgentEvent(
                            "background_done",
                            f"Specialist {spec.display_name} completed",
                            {
                                "task_id": task_id,
                                "name": spec.name,
                                "result": result.result or "",
                                "duration_ms": result.duration_ms,
                                "tools_used": list(result.tools_used),
                            },
                        ))
                    else:
                        await self._callback.on_event(AgentEvent(
                            "background_failed",
                            f"Specialist {spec.display_name} failed",
                            {
                                "task_id": task_id,
                                "name": spec.name,
                                "error": result.error or "",
                            },
                        ))
            except Exception:
                logger.debug("delegate-bg terminal event failed", exc_info=True)

        _bg = asyncio.create_task(
            _run_delegate_bg(),
            name=f"delegate-bg-{task_id}",
        )
        _background_tasks.add(_bg)
        _bg.add_done_callback(_background_tasks.discard)

        # Return immediately — foreground turn is now free for the next
        # user message. The specialist's result lands as a side-note on
        # the brain's next turn (via chat_ws subagent-terminal pump).
        if _fanout_group_id is not None:
            # Consolidation is wired: the result is delivered automatically
            # when the specialist settles — the brain must NOT promise to do
            # it "on my next reply" (there may be no next user turn).
            return (
                f"Started {spec.display_name} in the background. When it "
                f"finishes, its result is delivered to the user automatically "
                f"as a follow-up reply — you do NOT need to chase it. For THIS "
                f"turn, give a brief honest status (e.g. 'On it — "
                f"{spec.display_name} is working; I'll send the result "
                f"shortly'). You can keep typing in the meantime."
            )
        return (
            f"Started {spec.display_name} in the background. The "
            f"specialist is working on your task — I'll fold the result "
            f"into my next reply when it completes. You can keep typing "
            f"in the meantime."
        )

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
        except Exception as exc:
            logger.debug("Failed to load site knowledge for domain: %s", exc)

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


