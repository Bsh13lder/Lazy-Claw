"""Specialist runner — executes a single specialist as a mini agent loop.

Reuses the same LLM routing, tool execution, and permission checking
as the main agent, but with a filtered skill set and specialist-specific
system prompt.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from lazyclaw.config import load_config
from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.stuck_detector import detect_stuck
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.registry import SkillRegistry
from lazyclaw.skills.tool_namespace import (
    bare_tool_name,
    is_native_shadowed,
    native_names_from_tools,
    specialist_block_message,
)
from lazyclaw.teams.failure_report import (
    OUTCOME_ALL_TOOLS_FAILED,
    OUTCOME_CANCELLED,
    OUTCOME_EXCEPTION,
    OUTCOME_STUCK_LOOP,
    all_tools_failed,
    render_failure_report,
)
from lazyclaw.teams.learning import StepEntry
from lazyclaw.teams.specialist import (
    CODE_SPECIALIST,
    SpecialistConfig,
    code_workspace_dir,
)

logger = logging.getLogger(__name__)

# Code Specialist transcript caps. Keep step rows small so 50 of them
# can ride a single /api/agents/status response without bloating the
# payload — Web UI re-fetches every 3s.
_TS_ARGS_CAP = 120
_TS_RESULT_CAP = 200
_FILES_TOUCHED_CAP = 64  # absolute file count cap to keep payload tight


def _summarize(value: object, cap: int) -> str:
    """Shrink an args dict / tool result to a single-line preview."""
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:
        text = str(value)
    text = " ".join(text.split())  # collapse whitespace + newlines
    return text[:cap]


def _scan_workspace_files(workspace_dir: str) -> tuple[str, ...]:
    """Return relative paths of files claude-code wrote (deterministic order).

    Walks ``workspace_dir`` and returns at most ``_FILES_TOUCHED_CAP``
    paths relative to the dir. Hidden files / dirs (``.git``,
    ``__pycache__``, ``node_modules``) are skipped — they're noise that
    obscures the real deliverables. Best-effort: any FS error returns
    an empty tuple rather than propagating, so capture is non-fatal.
    """
    if not workspace_dir or not os.path.isdir(workspace_dir):
        return ()
    out: list[str] = []
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv"}
    try:
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(root, f), workspace_dir)
                out.append(rel)
                if len(out) >= _FILES_TOUCHED_CAP:
                    break
            if len(out) >= _FILES_TOUCHED_CAP:
                break
    except OSError as exc:
        logger.debug("workspace scan failed for %s: %s", workspace_dir, exc)
        return ()
    out.sort()
    return tuple(out)

# No hardcoded iteration cap — stuck_detector handles loop/error detection.
# Safety cap only prevents truly runaway loops (should never be hit).
MAX_ITERATIONS = 200
_NUDGE_AT = 30  # Nudge after 30 iterations to wrap up

# Max raw-data re-rolls the F1 grounding gate may force on the specialist's
# final reply before shipping with a [grounding-degraded] marker. Matches
# the brain's _F1_CONFAB_MAX_RETRIES posture — better one slightly imperfect
# reply than an infinite retry loop on a wedged worker.
_F1_GATE_MAX_RETRIES = 2


@dataclass(frozen=True)
class TranscriptStep:
    """One row in the per-turn transcript surfaced on CodeSpecialist.tsx.

    Built incrementally from the specialist's own AgentEvent stream so
    the Web UI can render a step timeline (tool name + arg summary +
    result preview + duration) instead of just `recent_tools` chips.
    Frozen so consumers can't mutate captured history.
    """

    kind: str            # "tool" | "thinking" | "phase"
    name: str            # tool name, phase name, etc.
    args_summary: str    # JSON-ish single-line preview (≤120 chars)
    result_summary: str  # tool output preview (≤200 chars)
    duration_ms: int     # wall-clock ms this step took
    success: bool = True
    error: str = ""


@dataclass(frozen=True)
class SpecialistResult:
    """Immutable result from a specialist run."""

    agent_name: str
    task: str
    result: str
    tools_used: tuple[str, ...]
    model_used: str
    duration_ms: int
    step_history: tuple[StepEntry, ...] = ()
    success: bool = True
    error: str | None = None
    # ── Code Specialist visibility (Code Specialist page) ──────────
    # Populated only when the run was a Code Specialist (other
    # specialists leave these empty; the Web UI hides panels gracefully
    # when fields are missing). The four together let the user see:
    #   - exactly what was sent to claude-code MCP (prompt_sent)
    #   - what claude-code did, step by step (transcript)
    #   - where the generated files landed (workspace_dir)
    #   - a clickable list of touched files (files_touched)
    prompt_sent: str = ""
    transcript: tuple[TranscriptStep, ...] = ()
    workspace_dir: str = ""
    files_touched: tuple[str, ...] = ()
    short_description: str = ""


# Single source of truth lives in skills/tool_namespace.py. Kept as a
# module-local alias so existing references (and tests) resolve unchanged.
_bare_tool_name = bare_tool_name


def _filter_tools(
    registry: SkillRegistry,
    allowed: tuple[str, ...],
    *,
    include_scraper: bool = False,
) -> list[dict]:
    """Return OpenAI-format tool list filtered to allowed skill names.

    When ``include_scraper`` is True, every connected mcp-scraper tool is
    unioned in. Scraper tool names are dynamic (`mcp_<server_uuid>_<name>`)
    so we identify them by their description containing ``mcp-scraper``.
    Without this, browser/research specialists would fall back to opening
    Chrome for read-only contact-data work that ``extract_entities`` solves
    in one JS-rendered call.
    """
    all_tools = registry.list_tools()
    allowed_set = set(allowed)
    out = [t for t in all_tools if t["function"]["name"] in allowed_set]
    if include_scraper:
        try:
            mcp_tools = registry.list_mcp_tools()
        except Exception:
            mcp_tools = []
        seen = {t["function"]["name"] for t in out}
        for t in mcp_tools:
            func = t.get("function", {})
            name = func.get("name", "")
            desc = (func.get("description") or "").lower()
            if "mcp-scraper" in desc and name not in seen:
                out.append(t)
                seen.add(name)
    # Union in MCP tools whose BARE suffix (after stripping mcp_<uuid>_) is
    # explicitly listed in the specialist's allowlist. MCP tool ids are
    # dynamic (mcp_<uuid>_<name>) so the exact-name match above can never hit
    # them — without this a specialist that lists e.g. `upwork_submit_proposal`
    # can never reach it (2026-06-08 apply-loop regression).
    #
    # BUT native wins on a name collision: if a native tool already owns that
    # bare name (e.g. the encrypted `create_sheet` vs a Google Workspace MCP
    # `create_sheet`), do NOT union the MCP twin in — the exact-name match
    # above already added the native one. Without this the documents_specialist
    # chased the Google `create_sheet` and stuck-looped (2026-06-08 22:05).
    native_names = native_names_from_tools(all_tools)
    try:
        _all_mcp = registry.list_mcp_tools()
    except Exception:
        _all_mcp = []
    _seen = {t["function"]["name"] for t in out}
    for t in _all_mcp:
        name = t.get("function", {}).get("name", "")
        bare = _bare_tool_name(name)
        if name and name not in _seen and bare in allowed_set and bare not in native_names:
            out.append(t)
            _seen.add(name)
    return out


async def run_specialist(
    user_id: str,
    specialist: SpecialistConfig,
    task: str,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker,
    callback=None,
    cancel_token=None,
    tab_context=None,
    *,
    project_tag: str | None = None,
    goal_id: str | None = None,
    task_id: str | None = None,
    code_session_id: str | None = None,
    on_session_id: Callable[[str], Awaitable[None]] | None = None,
) -> SpecialistResult:
    """Run a specialist agent loop for a single task.

    Uses the same agentic pattern as Agent.process_message but with:
    - Specialist-specific system prompt
    - Filtered tool set (only specialist.allowed_skills)
    - No conversation history (fresh context per task)
    - No message persistence (team lead handles storage)

    For the Code Specialist only: ``project_tag``, ``goal_id``, and
    ``task_id`` resolve a persistent workspace folder under
    /workspace/<tag>/<goal>/<task_id>/ that's auto-created and
    prepended to the prompt so claude-code lands its files there.
    The captured prompt, per-step transcript, and final files_touched
    list ride on ``SpecialistResult`` for the CodeSpecialist Web UI.
    """
    start_time = time.monotonic()
    tools_used: list[str] = []
    step_history: list[StepEntry] = []
    transcript: list[TranscriptStep] = []
    is_code = specialist.name == CODE_SPECIALIST.name

    # Resolve + create the per-task workspace dir (Code Specialist only).
    # Best-effort — if /workspace isn't writable for some reason, log and
    # carry on without enrichment so the task still runs.
    workspace_dir = ""
    if is_code:
        workspace_dir = code_workspace_dir(
            task_id=task_id or "task",
            project_tag=project_tag,
            goal_id=goal_id,
        )
        try:
            os.makedirs(workspace_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "code workspace mkdir failed for %s: %s — running without "
                "workspace enrichment", workspace_dir, exc,
            )
            workspace_dir = ""

    # Build filtered tools — when the specialist opts into scraper, every
    # connected mcp-scraper tool is unioned in so the worker can reach
    # extract_entities / crawl_url / batch_crawl without us having to
    # hard-code dynamic tool IDs.
    filtered_tools = _filter_tools(
        registry,
        specialist.allowed_skills,
        include_scraper=specialist.include_scraper,
    )
    # Native tool names — used to enforce native-primacy on name collisions
    # (a Google MCP `create_sheet` must never shadow the native one).
    try:
        native_names = native_names_from_tools(registry.list_tools())
    except Exception:
        native_names = frozenset()

    # Build executor (reuses same registry + permission checker).
    # Pass config so the auto-recorder in tool_executor can save lessons.
    _config = load_config()
    executor = ToolExecutor(
        registry, permission_checker=permission_checker, config=_config,
    )

    # Workspace hint — only the Code Specialist gets one. claude-code's
    # Bash/Read/Write tools run with cwd=/workspace (set in mcp/manager.py),
    # so prefixing the prompt with the per-task subdir makes the very
    # first action a `cd` into it — generated files land in the right
    # bucket without any host-side cleanup. We also call this out in the
    # response prose so the brain can echo the path back to the user.
    _workspace_hint = ""
    if is_code and workspace_dir:
        _workspace_hint = (
            f"\n\nWORKSPACE: {workspace_dir}\n"
            f"All code, configs, and notes you generate MUST be written under "
            f"this directory. As your first action when calling claude-code, "
            f"`cd {workspace_dir}` so every subsequent Bash/Read/Write tool is "
            f"rooted there. Mention the path in your summary so the user can "
            f"find the files on disk.\n"
        )

    # ── ADR-0005 Phase 6: auto-improving specialists ──────────────────
    # Recall this specialist's own past lessons (success/failure shapes)
    # via the ADR-0002 Skill-Lesson Learning Loop (scoped by specialist
    # name) and prepend them so the specialist gets better the more it is
    # used. Fire-and-forget: any failure degrades to an empty block, so a
    # lesson-recall problem can never break a specialist run.
    _lessons_block = ""
    try:
        from lazyclaw.runtime.specialist_lessons import (
            recall_specialist_lessons,
        )

        _lessons_block = await recall_specialist_lessons(
            _config, user_id, specialist.name, task,
        )
    except Exception:
        logger.debug(
            "specialist lesson recall failed for %s — continuing without",
            specialist.name, exc_info=True,
        )
        _lessons_block = ""
    _lessons_section = f"\n\n{_lessons_block}\n" if _lessons_block else ""

    # System prompt = specialist prompt + workspace hint + learned lessons + task
    system_prompt = (
        f"{specialist.system_prompt}"
        f"{_workspace_hint}"
        f"{_lessons_section}\n\n"
        f"---\n\n"
        f"Your task:\n{task}\n\n"
        f"Complete this task using your available tools. "
        f"When done, provide a clear summary of your findings or results."
    )

    # Captured exactly once so the Web UI can display "the prompt sent"
    # without us having to re-derive it. Includes the workspace hint so
    # the user sees the full instruction claude-code received.
    prompt_sent_full = f"{system_prompt}\n\nUSER: {task}"

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=task),
    ]

    model_used = specialist.preferred_model or "default"

    # Stuck detection state — tracks tool names and results across iterations
    _tool_history: list[str] = []
    _tool_results: list[str] = []
    # F1 grounding gate state. On a channel-read turn the final reply is
    # checked against the fresh tool data (composing the same detectors the
    # brain uses). A violation re-rolls the worker with the raw data
    # injected, bounded to ``_F1_GATE_MAX_RETRIES`` so a wedged worker can't
    # loop forever — after that it ships with a ``[grounding-degraded]``
    # prefix. The gate fails OPEN, so this only ever fires on a real
    # confabulation, never on a detector crash.
    _f1_retries: int = 0
    # One-shot rescue: when stuck looping on `browser`, inject a web_search hint
    # and let the worker try ONE more iteration before bailing. Strictly capped
    # at one rescue per task to prevent infinite cycles.
    _rescue_used: bool = False

    # P1 session-id state. ``_active_session_id`` carries through into
    # eco_router.chat kwargs every iteration so the worker's session stays
    # stable across THIS dispatch (and resumes if a prior dispatch passed
    # in code_session_id). ``_session_notified`` latches on_session_id so we
    # fire the callback AT MOST ONCE per run (no overwrite churn on the DB
    # if the provider returns the same id every call).
    _active_session_id: str | None = code_session_id if is_code else None
    _session_notified: bool = False
    # Defensive retry guard for stale-resume: if the provider returns a
    # session-not-found error AND we were trying to --resume, we retry the
    # SAME iteration once with a fresh session.
    _stale_session_retry_used: bool = False

    # Single point of SpecialistResult construction so every return path
    # carries the Code Specialist visibility fields. The bare ``run_specialist``
    # body (5+ return points across cancel/done/stuck/timeout/error) used
    # to drift — passing 8 extra kwargs per return is bug-prone. The
    # builder snapshots the live transcript + scans the workspace dir
    # for files_touched at the moment of return.
    def _build_result(
        *,
        result: str,
        success: bool = True,
        error: str | None = None,
    ) -> SpecialistResult:
        duration = int((time.monotonic() - start_time) * 1000)
        files = _scan_workspace_files(workspace_dir) if is_code else ()
        short_desc = (
            task.strip().splitlines()[0][:120] if task else specialist.display_name
        )
        return SpecialistResult(
            agent_name=specialist.name,
            task=task,
            result=result,
            tools_used=tuple(tools_used),
            model_used=model_used,
            duration_ms=duration,
            step_history=tuple(step_history),
            success=success,
            error=error,
            prompt_sent=prompt_sent_full if is_code else "",
            transcript=tuple(transcript) if is_code else (),
            workspace_dir=workspace_dir if is_code else "",
            files_touched=files,
            short_description=short_desc if is_code else "",
        )

    # Failure-shaped terminations record a structured report as the
    # result text (Claude Code orchestrator pattern) so the brain's
    # consolidation turn gets parseable data — which specialist, what it
    # was asked, why it died, which tools it burned — instead of an
    # opaque "[Stuck: loop]" blob it papers over with a false "✅ done".
    # See lazyclaw/teams/failure_report.py (2026-06-10 incident).
    def _failure_report(outcome: str, detail: str = "") -> str:
        return render_failure_report(
            specialist=specialist.name,
            task=task,
            outcome=outcome,
            tool_history=_tool_history,
            tool_results=_tool_results,
            detail=detail,
        )

    try:
        for _iteration in range(MAX_ITERATIONS):
            if cancel_token and cancel_token.is_cancelled:
                return _build_result(
                    result=_failure_report(
                        OUTCOME_CANCELLED, detail="Cancelled by user",
                    ),
                    success=False, error="Cancelled by user",
                )

            # Running-long nudge at 80% of cap
            if _iteration == _NUDGE_AT:
                messages.append(LLMMessage(
                    role="system",
                    content=(
                        "You've been working for a while. Wrap up: summarize what "
                        "you've done and what's left. If task is incomplete, report "
                        "partial results — don't keep going silently."
                    ),
                ))
                logger.info(
                    "Specialist %s: nudge at iteration %d/%d",
                    specialist.name, _iteration + 1, MAX_ITERATIONS,
                )

            # Prune old tool results to save tokens
            if _iteration > 0:
                from lazyclaw.runtime.agent import _prune_old_tool_results
                messages = _prune_old_tool_results(messages, keep_last_n=2)

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
            # P1: Pass the worker session id through eco_router → provider so
            # claude_cli/sdk providers can use --session-id (first call) or
            # --resume (subsequent). Only injected on the Code Specialist
            # since other specialists treat each run as standalone.
            if is_code and _active_session_id:
                kwargs["session_id"] = _active_session_id

            # Workers use ROLE_WORKER — eco_router picks the right model
            # (Gemma 4 local in HYBRID, Haiku in FULL, etc.)
            logger.info("Worker %s iteration %d", specialist.name, _iteration + 1)
            try:
                response = await eco_router.chat(
                    messages, user_id=user_id, role=ROLE_WORKER, **kwargs
                )
            except Exception as exc:
                # Stale-resume defense: if --resume failed with a session-
                # not-found-style error, retry ONCE with a fresh session.
                # Bounded to one retry to avoid masking real provider errors.
                _err = str(exc).lower()
                _looks_stale = (
                    is_code
                    and _active_session_id
                    and not _stale_session_retry_used
                    and ("session" in _err and ("not found" in _err or "expired" in _err or "unknown" in _err))
                )
                if not _looks_stale:
                    raise
                logger.warning(
                    "Specialist %s: resume session %s appears stale (%s) — "
                    "retrying with fresh session",
                    specialist.name, _active_session_id, exc,
                )
                _stale_session_retry_used = True
                _active_session_id = None
                kwargs.pop("session_id", None)
                response = await eco_router.chat(
                    messages, user_id=user_id, role=ROLE_WORKER, **kwargs
                )

            model_used = response.model or model_used

            # P1: Capture the (new or existing) session id from the provider's
            # usage dict and notify the caller exactly once per dispatch.
            # Subsequent iterations within the same dispatch reuse the id
            # via _active_session_id above.
            if is_code:
                _resp_sid = None
                try:
                    _resp_sid = (response.usage or {}).get("session_id")
                except Exception:
                    _resp_sid = None
                if _resp_sid:
                    if not _active_session_id:
                        _active_session_id = str(_resp_sid)
                    elif _active_session_id != str(_resp_sid):
                        # Provider rotated the id (e.g. after stale-resume retry).
                        # Switch over so the next iteration uses the new id.
                        _active_session_id = str(_resp_sid)
                        _session_notified = False  # force re-notify on the new id
                    if on_session_id and not _session_notified:
                        try:
                            await on_session_id(_active_session_id)
                            _session_notified = True
                        except Exception:
                            logger.debug(
                                "on_session_id callback raised — ignoring",
                                exc_info=True,
                            )

            if not response.tool_calls:
                # Final response — specialist is done.
                #
                # F1 grounding gate (specialist-path anti-confabulation).
                # The brain runs a mechanical F1 backstop on every
                # channel-read turn; the specialist path historically ran
                # none and shipped the worker's reply raw. We close that
                # gap here by composing the SAME detectors via
                # ``f1_gate.evaluate_grounding``. On a non-channel-read
                # turn the gate is observe-only (returns ok=True), so this
                # is a no-op for research/code/web specialists.
                #
                # On a grounding violation with retry budget left, we
                # inject the raw tool data and re-roll the worker once
                # (bounded by ``_F1_GATE_MAX_RETRIES``). When the budget is
                # exhausted we ship anyway with a ``[grounding-degraded]``
                # prefix + a loud warning — better one imperfect reply than
                # an infinite loop. The gate fails OPEN, so a detector
                # crash can never block the specialist.
                _final = response.content or ""
                try:
                    from lazyclaw.runtime.f1_gate import evaluate_grounding

                    _verdict = evaluate_grounding(
                        _final, _tool_history, _tool_results,
                    )
                except Exception:
                    logger.debug(
                        "Specialist %s: F1 gate raised — shipping reply "
                        "un-gated", specialist.name, exc_info=True,
                    )
                    _verdict = None

                if (
                    _verdict is not None
                    and not _verdict.ok
                    and _verdict.corrective_injection
                    and _f1_retries < _F1_GATE_MAX_RETRIES
                ):
                    logger.warning(
                        "specialist F1 gate: re-rolling on grounding "
                        "violation (%s) — retry %d of %d for %s",
                        _verdict.reason,
                        _f1_retries + 1,
                        _F1_GATE_MAX_RETRIES,
                        specialist.name,
                    )
                    messages.append(LLMMessage(
                        role="user",
                        content=_verdict.corrective_injection,
                    ))
                    _f1_retries += 1
                    continue

                # Every tool call errored — the worker "finished", but it
                # never got a single good result. Append the structured
                # report so the consolidating brain sees the blocker
                # instead of trusting the worker's prose. success stays
                # True: this is a normal completion for the delegate /
                # background_done routing; only the CONTENT is annotated.
                _report_suffix = ""
                if all_tools_failed(_tool_history, _tool_results):
                    logger.warning(
                        "Specialist %s finished but ALL %d tool calls "
                        "errored — appending failure report",
                        specialist.name, len(_tool_history),
                    )
                    _report_suffix = "\n\n" + _failure_report(
                        OUTCOME_ALL_TOOLS_FAILED,
                        detail="Specialist produced a final reply, but "
                               "every tool call it made returned an error.",
                    )

                if _verdict is not None and not _verdict.ok:
                    # Retries exhausted — ship with a degraded marker + a
                    # loud log so production can grep for residual leaks.
                    logger.warning(
                        "specialist F1 gate: retries exhausted (%d/%d) for "
                        "%s — shipping [grounding-degraded]. reason=%s",
                        _f1_retries,
                        _F1_GATE_MAX_RETRIES,
                        specialist.name,
                        _verdict.reason,
                    )
                    return _build_result(
                        result=f"[grounding-degraded] {_final}{_report_suffix}",
                    )

                return _build_result(result=f"{_final}{_report_suffix}")

            # Process tool calls
            assistant_msg = LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_msg)

            for tc in response.tool_calls:
                # Log what the specialist is calling (critical for debugging)
                _args_summary = {k: (str(v)[:80] if isinstance(v, str) else v)
                                 for k, v in (tc.arguments or {}).items()
                                 if not k.startswith("_")}
                logger.info(
                    "Specialist %s step %d: %s(%s)",
                    specialist.name, _iteration + 1, tc.name, _args_summary,
                )

                # Only execute if skill is in allowed list. Scraper-opted
                # specialists also accept any mcp-scraper tool — these are
                # dynamic tool IDs unioned in by ``_filter_tools`` above
                # (see SpecialistConfig.include_scraper).
                _is_scraper_tool = (
                    specialist.include_scraper
                    and tc.name.startswith("mcp_")
                    and any(
                        t["function"]["name"] == tc.name
                        and "mcp-scraper" in (t["function"].get("description") or "").lower()
                        for t in filtered_tools
                    )
                )
                # Native wins on a name collision: an MCP tool whose bare name
                # matches a native tool (e.g. a Google `create_sheet`) is NOT
                # accepted via the bare-suffix allowance — the worker is
                # redirected to its native twin below instead of executing the
                # external tool. Preserves genuine MCP-only allowlist entries.
                _is_allowed_mcp = (
                    tc.name.startswith("mcp_")
                    and _bare_tool_name(tc.name) in specialist.allowed_skills
                    and not is_native_shadowed(tc.name, native_names)
                )
                _step_started = time.monotonic()
                if (
                    tc.name not in specialist.allowed_skills
                    and not _is_scraper_tool
                    and not _is_allowed_mcp
                ):
                    tool_result = specialist_block_message(
                        tc.name,
                        specialist.display_name,
                        native_names,
                        specialist.allowed_skills,
                    )
                else:
                    # Inject TabContext for browser isolation (immutable — new ToolCall)
                    exec_tc = tc
                    if tab_context and tc.name == "browser":
                        exec_tc = ToolCall(
                            id=tc.id,
                            name=tc.name,
                            arguments={**tc.arguments, "_tab_context": tab_context},
                        )
                    tool_result = await executor.execute(exec_tc, user_id, callback=callback)

                    # If approval required, note it but don't block
                    if isinstance(tool_result, str) and tool_result.startswith(APPROVAL_PREFIX):
                        tool_result = (
                            f"Tool '{tc.name}' requires user approval and cannot be used "
                            f"in team mode right now. Skip this tool and work with what you have."
                        )

                    tools_used.append(tc.name)
                    _is_error = isinstance(tool_result, str) and tool_result.startswith("Error")
                    step_history.append(StepEntry(
                        tool_name=tc.name,
                        action=(tc.arguments or {}).get("action"),
                        target=(tc.arguments or {}).get("target"),
                        success=not _is_error,
                        error_snippet=tool_result[:200] if _is_error else "",
                        iteration=_iteration,
                    ))
                    _result_len = len(tool_result) if isinstance(tool_result, str) else 0
                    if _result_len > 500:
                        logger.debug(
                            "Specialist %s: %s returned %d chars",
                            specialist.name, tc.name, _result_len,
                        )

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

                # Track for stuck detection
                _tool_history.append(tc.name)
                _tool_results.append(
                    tool_result if isinstance(tool_result, str) else str(tool_result)
                )

                # Per-step transcript row — Code Specialist only. Captures
                # every tool execution (allowed + denied) so the user can
                # see the full sequence on CodeSpecialist.tsx, including
                # ones we blocked. Cheap: 4 strings + 1 int per call.
                if is_code:
                    _is_step_error = (
                        isinstance(tool_result, str)
                        and tool_result.startswith("Error")
                    )
                    transcript.append(TranscriptStep(
                        kind="tool",
                        name=tc.name,
                        args_summary=_summarize(tc.arguments or {}, _TS_ARGS_CAP),
                        result_summary=_summarize(tool_result, _TS_RESULT_CAP),
                        duration_ms=int((time.monotonic() - _step_started) * 1000),
                        success=not _is_step_error,
                        error=tool_result[:200] if _is_step_error else "",
                    ))

            # ── Stuck detection after processing all tool calls ──
            stuck = detect_stuck(_tool_history, _tool_results, _tool_results[-1] if _tool_results else None)
            if stuck:
                # One-shot rescue: if the worker is stuck looping on `browser`
                # and `web_search` is in its toolbox, inject a hint and let it
                # try ONE more iteration. This catches the common failure where
                # workers reach for a heavyweight scrape when a `site:` query
                # would have answered the question.
                if (
                    not _rescue_used
                    and stuck.reason == "loop"
                    and _tool_history
                    and _tool_history[-1] == "browser"
                    and "web_search" in (specialist.allowed_skills or ())
                ):
                    logger.info(
                        "Specialist %s stuck on browser — injecting web_search rescue hint",
                        specialist.name,
                    )
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "RESCUE: You looped on `browser`. STOP using browser for this turn. "
                            "Try `web_search` instead with a Google operator like "
                            "`site:domain.com <field you need>`. The URL in the search result "
                            "often contains the answer (e.g. instagram.com/<handle>/). "
                            "If web_search also fails, summarize what you DO have and exit."
                        ),
                    ))
                    # Reset the recent tool-history window so detect_stuck
                    # doesn't fire again on the next iteration just because of
                    # leftover browser entries.
                    _tool_history = _tool_history[:-3] if len(_tool_history) >= 3 else []
                    _rescue_used = True
                    continue  # let the worker loop run one more iteration with the hint

                logger.warning(
                    "Specialist %s stuck: %s (%s)",
                    specialist.name, stuck.reason, stuck.context,
                )
                # "loop" maps to the canonical stuck_loop outcome; other
                # stuck reasons (captcha, repeated_error, same_result, …)
                # keep the stuck_ prefix so consumers can match the family.
                _outcome = (
                    OUTCOME_STUCK_LOOP if stuck.reason == "loop"
                    else f"stuck_{stuck.reason}"
                )
                return _build_result(
                    result=_failure_report(
                        _outcome,
                        detail=(
                            f"{stuck.context} Completed {len(tools_used)} "
                            f"tool calls before getting stuck."
                        ),
                    ),
                    success=False,
                    error=stuck.context,
                )

        # Max iterations reached
        last_content = messages[-1].content if messages else "Max iterations reached."
        return _build_result(result=f"[Reached max iterations] {last_content}")

    except Exception as exc:
        logger.error("Specialist %s failed: %s", specialist.name, exc)
        # Report rendering is itself guarded — a formatting bug must
        # never mask the original failure.
        try:
            _report = _failure_report(
                OUTCOME_EXCEPTION,
                detail=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.debug("failure-report render failed", exc_info=True)
            _report = ""
        return _build_result(result=_report, success=False, error=str(exc))
