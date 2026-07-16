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
    pop_continuation_instruction,
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


def _compose_code_instruction(
    goal: Goal,
    *,
    additional_instruction: str | None = None,
) -> str:
    """Build the instruction sent to the code specialist.

    Two modes:

    1. **Fresh dispatch** (``additional_instruction`` is None) — combines
       the user's original ask, AI-drafted summary, plan steps, and
       Q/A pairs into a single brief. Sent on the FIRST turn so the
       worker has all the context it needs upfront.
    2. **Continuation** (``additional_instruction`` is set) — the worker
       brain is being resumed with ``--resume <session_id>``, so it
       already remembers the original brief and what it did last turn.
       Sending the whole plan again would burn tokens AND confuse it.
       We send ONLY the new turn's instruction plus a one-line reminder
       that this is a continuation of the same goal.
    """
    if additional_instruction is not None:
        return (
            f"# Continuing goal — same project\n"
            f"_Goal id `{goal.id[:12]}` — your prior session has been "
            f"resumed. The WORKSPACE path in your system prompt is the "
            f"same as last turn._\n\n"
            f"## New instruction\n{additional_instruction.strip()}"
        )

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
            "[codegoal] runtime not registered — goal=%s stays in "
            "EXECUTING with no dispatch. Call register_runtime() from app.py.",
            goal.id,
        )
        return

    # Lazy imports so this module stays cheap to import at startup and
    # doesn't pull the LLM / specialist stack into the goal-executor's
    # module graph (which itself runs at gateway boot).
    from lazyclaw.teams.runner import run_specialist
    from lazyclaw.teams.specialist import CODE_SPECIALIST

    # P1: Continuation re-entry from GoalExecutor.continue_code stashes
    # the turn-scoped instruction in goal_executor._CONTINUATION_INSTRUCTIONS.
    # Pop here so the side-channel doesn't leak past one dispatch. None
    # means this is a fresh dispatch from intake/answers.
    continuation = pop_continuation_instruction(goal.id)
    instruction = _compose_code_instruction(
        goal, additional_instruction=continuation,
    )
    logger.debug(
        "[codegoal] dispatch goal=%s mode=%s prior_session=%s",
        goal.id, "continuation" if continuation is not None else "fresh",
        goal.code_session_id[:8] if goal.code_session_id else None,
    )

    # Use the goal_id itself as the task_id for trivial cross-reference:
    # CodeSpecialist.tsx already groups by project_tag and shows the
    # goal_id header pill; reusing the id keeps the workspace dir name
    # stable + searchable.
    task_id = f"goal-{goal.id[:12]}"
    project_tag = goal.work_type or "code-goal"

    # P1: persistent worker session — pass the prior session id (None on
    # first dispatch) and a latching callback that writes the FIRST id
    # returned back to the Goal so the next dispatch can --resume it.
    async def _on_session_id(sid: str) -> None:
        try:
            executor = GoalExecutor(cfg, GoalRepository(cfg))
            await executor.set_code_session_id(goal.user_id, goal.id, sid)
            logger.debug(
                "[codegoal] session id latched goal=%s session=%s",
                goal.id, sid[:8] if sid else sid,
            )
        except Exception:
            logger.exception(
                "[codegoal] set_code_session_id failed goal=%s user=%s",
                goal.id, goal.user_id,
            )

    async def _run_and_finalize() -> None:
        logger.debug(
            "[codegoal] run_specialist starting goal=%s task=%s resume=%s",
            goal.id, task_id, bool(goal.code_session_id),
        )
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
                code_session_id=goal.code_session_id,
                on_session_id=_on_session_id,
            )
        except Exception as exc:
            logger.exception(
                "[codegoal] specialist crashed goal=%s task=%s error_type=%s",
                goal.id, task_id, type(exc).__name__,
            )
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
                    "[codegoal] team_lead mirror failed goal=%s task=%s",
                    goal.id, task_id, exc_info=True,
                )

        # P1: Code goals are multi-turn. A successful turn does NOT
        # auto-terminate the goal — the user (or a downstream signal) marks
        # it DONE explicitly when the project is fully delivered. Until
        # then the goal stays EXECUTING so the next "now add tests" /
        # "deploy it" message can continue_code into the SAME session.
        # Failures still go to BLOCKED so the user can unblock + continue
        # (mark_blocked is the existing retry-friendly transition).
        if result.success:
            logger.debug(
                "[codegoal] run_specialist succeeded goal=%s task=%s files=%d",
                goal.id, task_id, len(result.files_touched or ()),
            )
            await _safe_touch_progress(
                cfg, goal,
                f"turn complete — files: {len(result.files_touched or ())}",
            )
        else:
            logger.warning(
                "[codegoal] run_specialist failed goal=%s task=%s",
                goal.id, task_id,
            )
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
                "[codegoal] team_lead.register failed goal=%s task=%s",
                goal.id, task_id, exc_info=True,
            )

    _bg = asyncio.create_task(_run_and_finalize(), name=f"code-goal-{goal.id[:8]}")
    _background_tasks.add(_bg)
    _bg.add_done_callback(_background_tasks.discard)


# ── Internal helpers ────────────────────────────────────────────────


async def _safe_mark_done(config: Config, goal: Goal, result: str) -> None:
    """Move the goal to DONE. ``result`` is captured on the bg task
    record; ``mark_done`` itself just transitions state — no payload.

    Reserved for explicit user-driven completion (``/goal done <id>``);
    NOT called automatically anymore — see ``_run_and_finalize`` above.
    """
    try:
        executor = GoalExecutor(config, GoalRepository(config))
        await executor.mark_done(goal.user_id, goal.id)
        logger.debug("[codegoal] mark_done succeeded goal=%s", goal.id)
    except Exception:
        logger.exception(
            "[codegoal] mark_done failed goal=%s user=%s",
            goal.id, goal.user_id,
        )


async def _safe_touch_progress(config: Config, goal: Goal, note: str) -> None:
    """Bump last_progress_at + last_action without changing status.

    Used after a successful continuation turn to record activity so the
    Goals UI shows fresh progress without auto-terminating the goal.
    """
    try:
        from dataclasses import replace
        import time as _time
        repo = GoalRepository(config)
        current = await repo.get(goal.user_id, goal.id)
        if current is None:
            logger.debug(
                "[codegoal] touch_progress no-op: goal=%s not found",
                goal.id,
            )
            return
        await repo.update(replace(
            current,
            last_action=note[:200],
            last_progress_at=_time.time(),
        ))
    except Exception:
        logger.exception(
            "[codegoal] touch_progress failed goal=%s user=%s",
            goal.id, goal.user_id,
        )


async def _safe_mark_failed(config: Config, goal: Goal, reason: str) -> None:
    """Move the goal to BLOCKED with a clear reason so the user can
    re-run the dispatch or hand-tweak the answers. ``mark_blocked`` is
    the public surface; the private ``_fail`` lands in FAILED but isn't
    re-entrant by the user."""
    try:
        executor = GoalExecutor(config, GoalRepository(config))
        await executor.mark_blocked(goal.user_id, goal.id, reason[:500])
        logger.debug("[codegoal] mark_blocked succeeded goal=%s", goal.id)
    except Exception:
        logger.exception(
            "[codegoal] mark_blocked failed goal=%s user=%s",
            goal.id, goal.user_id,
        )


# ── Register at import time ─────────────────────────────────────────

register_default_dispatch("code", dispatch_code_goal)
