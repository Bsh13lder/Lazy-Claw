"""Dispatch a Code Specialist run when a code-tagged Goal hits EXECUTING.

Companion to ``contract_intake_executor`` but scoped to "build me a
thing" goals where the user has answered the AI's clarifying questions
and the next step is to actually generate code via the
``code_specialist`` (which itself routes to the claude-code MCP).

Flow:

  1. ``GoalExecutor.start(account_slug="code")`` drafts a plan + asks
     clarifying questions.
  2. User answers via the Web UI intake modal — every answered batch
     calls ``GoalExecutor.submit_answers``.
  3. When the last question is answered the goal transitions to
     EXECUTING and ``_dispatch`` looks us up via
     ``_DEFAULT_DISPATCH_BY_SLUG["code"]``.
  4. We compose the title + plan steps + Q/A pairs into a single
     instruction, then fire the ``code_specialist`` as a fire-and-forget
     background task tagged with ``goal_id`` so the Code Specialist Web
     UI page renders the per-goal header + clickable workspace folder.
  5. When the SpecialistResult lands, we call
     ``GoalExecutor.mark_done`` (or ``_fail`` on error) so the goal row
     leaves EXECUTING.

The dispatch returns IMMEDIATELY so the calling API request (the
intake modal's "submit answers") doesn't hang on a multi-minute
specialist run. Live progress is visible on the Code Specialist page
via the existing ``/api/agents/status`` polling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lazyclaw.config import Config
from lazyclaw.runtime.goal_executor import (
    Goal,
    GoalExecutor,
    GoalRepository,
    register_default_dispatch,
)

logger = logging.getLogger(__name__)


# ── Runtime injection (set by app.py at startup) ───────────────────

_DEFAULT_CONFIG: Config | None = None
_DEFAULT_REGISTRY: Any | None = None
_DEFAULT_ECO_ROUTER: Any | None = None
_DEFAULT_PERMISSION_CHECKER: Any | None = None
_DEFAULT_TEAM_LEAD: Any | None = None


def register_runtime(
    config: Config,
    registry: Any,
    eco_router: Any,
    permission_checker: Any | None = None,
    team_lead: Any | None = None,
) -> None:
    """Wire app-level singletons so dispatch can spawn the specialist.

    Idempotent — safe to call from multiple gateway init paths.
    """
    global _DEFAULT_CONFIG, _DEFAULT_REGISTRY, _DEFAULT_ECO_ROUTER
    global _DEFAULT_PERMISSION_CHECKER, _DEFAULT_TEAM_LEAD
    _DEFAULT_CONFIG = config
    _DEFAULT_REGISTRY = registry
    _DEFAULT_ECO_ROUTER = eco_router
    _DEFAULT_PERMISSION_CHECKER = permission_checker
    _DEFAULT_TEAM_LEAD = team_lead


# Prevent GC from killing fire-and-forget specialist tasks.
_background_tasks: set[asyncio.Task] = set()


def _compose_code_instruction(goal: Goal) -> str:
    """Build the one-shot instruction sent to the code specialist.

    Combines the user's original ask (``title``), the AI-drafted summary,
    the structured plan steps the goal already carries, and the verbatim
    Q/A pairs so claude-code has the full context in a single message.
    Keeps the brain from re-asking the same questions inside the
    specialist's loop.
    """
    parts: list[str] = [f"# Build request\n{goal.title.strip()}"]

    if goal.summary and goal.summary.strip() != goal.title.strip():
        parts.append(f"\n## Summary\n{goal.summary.strip()}")

    if goal.plan:
        step_lines = "\n".join(
            f"- {s.description}" for s in goal.plan if s.description
        )
        if step_lines:
            parts.append(f"\n## Plan steps\n{step_lines}")

    if goal.answers:
        qa_lines = "\n".join(
            f"**Q:** {q}\n**A:** {a}" for q, a in goal.answers.items()
        )
        parts.append(f"\n## Clarifying answers from the user\n{qa_lines}")

    if goal.risks:
        risk_lines = "\n".join(f"- {r}" for r in goal.risks if r)
        if risk_lines:
            parts.append(f"\n## Known risks / things to watch\n{risk_lines}")

    parts.append(
        "\n## Output expectation\n"
        "Use the claude-code MCP for the actual implementation. "
        "Land every file under the WORKSPACE path that will be injected "
        "into your system prompt. When done, summarize what you built, "
        "list the files you wrote, and call out anything that needs the "
        "user's attention (env vars, manual install steps, etc.)."
    )

    return "\n".join(parts)


# ── Public dispatch entry point ─────────────────────────────────────


async def dispatch_code_goal(goal: Goal) -> None:
    """Called by ``GoalExecutor._dispatch`` when a code goal hits EXECUTING.

    Returns immediately after spawning the specialist as a fire-and-forget
    task so the intake API doesn't hang. The Goal stays in EXECUTING until
    the specialist's terminal callback marks it done or failed.
    """
    cfg = _DEFAULT_CONFIG
    registry = _DEFAULT_REGISTRY
    eco_router = _DEFAULT_ECO_ROUTER
    perm = _DEFAULT_PERMISSION_CHECKER
    team_lead = _DEFAULT_TEAM_LEAD
    if cfg is None or registry is None or eco_router is None:
        logger.warning(
            "code_goal_executor: runtime not registered — goal %s stays in "
            "EXECUTING with no dispatch. Call register_runtime() from app.py.",
            goal.id,
        )
        return

    # Lazy imports so this module stays cheap to import at startup and
    # doesn't pull the LLM / specialist stack into the goal-executor's
    # module graph (which itself runs at gateway boot).
    from lazyclaw.teams.runner import run_specialist
    from lazyclaw.teams.specialist import CODE_SPECIALIST

    instruction = _compose_code_instruction(goal)

    # Use the goal_id itself as the task_id for trivial cross-reference:
    # CodeSpecialist.tsx already groups by project_tag and shows the
    # goal_id header pill; reusing the id keeps the workspace dir name
    # stable + searchable.
    task_id = f"goal-{goal.id[:12]}"
    project_tag = goal.work_type or "code-goal"

    async def _run_and_finalize() -> None:
        try:
            result = await run_specialist(
                user_id=goal.user_id,
                specialist=CODE_SPECIALIST,
                task=instruction,
                registry=registry,
                eco_router=eco_router,
                permission_checker=perm,
                callback=None,
                project_tag=project_tag,
                goal_id=goal.id,
                task_id=task_id,
            )
        except Exception as exc:
            logger.exception("code_goal_executor: specialist crashed for goal %s", goal.id)
            await _safe_mark_failed(cfg, goal, f"specialist crashed: {exc!s}")
            return

        # Mirror into team_lead so the Code Specialist page sees the
        # final state (the per-step transcript already streamed live via
        # specialist events; this is just the terminal mark).
        if team_lead is not None:
            try:
                if result.success:
                    team_lead.complete(
                        task_id,
                        result_preview=(result.result or "")[:100],
                        result_full=result.result or "",
                        mcp_prompt=result.prompt_sent,
                        mcp_transcript=tuple(
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
                        ),
                        workspace_dir=result.workspace_dir,
                        files_touched=tuple(result.files_touched or ()),
                        short_description=result.short_description,
                    )
                else:
                    team_lead.fail(task_id, error=result.error or "")
            except Exception:
                logger.debug(
                    "code_goal_executor: team_lead mirror failed",
                    exc_info=True,
                )

        # Mark goal terminal so it leaves EXECUTING.
        if result.success:
            await _safe_mark_done(cfg, goal, result.result or "")
        else:
            await _safe_mark_failed(cfg, goal, result.error or "specialist failed")

    # Register with team_lead so the Code Specialist page sees a card
    # immediately — without this the user sees nothing until the
    # specialist's first event fires.
    if team_lead is not None:
        try:
            team_lead.register(
                task_id=task_id,
                name=CODE_SPECIALIST.name,
                description=instruction[:80],
                lane="specialist",
                instruction_full=instruction,
                user_id=goal.user_id,
                project_tag=project_tag,
                goal_id=goal.id,
            )
        except Exception:
            logger.debug(
                "code_goal_executor: team_lead.register failed",
                exc_info=True,
            )

    _bg = asyncio.create_task(_run_and_finalize(), name=f"code-goal-{goal.id[:8]}")
    _background_tasks.add(_bg)
    _bg.add_done_callback(_background_tasks.discard)


# ── Internal helpers ────────────────────────────────────────────────


async def _safe_mark_done(config: Config, goal: Goal, result: str) -> None:
    """Move the goal to DONE. ``result`` is captured on the bg task
    record; ``mark_done`` itself just transitions state — no payload."""
    try:
        executor = GoalExecutor(config, GoalRepository(config))
        await executor.mark_done(goal.user_id, goal.id)
    except Exception:
        logger.exception("code_goal_executor: mark_done failed for goal %s", goal.id)


async def _safe_mark_failed(config: Config, goal: Goal, reason: str) -> None:
    """Move the goal to BLOCKED with a clear reason so the user can
    re-run the dispatch or hand-tweak the answers. ``mark_blocked`` is
    the public surface; the private ``_fail`` lands in FAILED but isn't
    re-entrant by the user."""
    try:
        executor = GoalExecutor(config, GoalRepository(config))
        await executor.mark_blocked(goal.user_id, goal.id, reason[:500])
    except Exception:
        logger.exception("code_goal_executor: mark_blocked failed for goal %s", goal.id)


# ── Register at import time ─────────────────────────────────────────

register_default_dispatch("code", dispatch_code_goal)
