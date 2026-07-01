"""Background task runner — parallel agent execution.

Each background task gets a fresh Agent instance and runs independently,
allowing the user to keep chatting while tasks execute.

Notifications push to Telegram and server dashboard via callbacks.

Brain fan-out consolidation
---------------------------

When the brain spawns N parallel ``run_background`` calls in a single
TAOR turn (because the user asked for "find emails for these 3 places"
and the dispatch_subagents same-shape rejector bounced the request),
each task fires its own per-task push by default. That produces N
"✅ Background task X done" Telegram messages and the brain never gets
to write ONE consolidated summary the way the user expects.

The fix lives in this file: any submit() with ``source="brain"`` and a
shared ``fanout_group_id`` is bucketed into ``_brain_groups``. Per-task
pushes for those tasks are suppressed; instead, when the LAST task in a
group settles, the runner synthesises a system-style instruction that
includes every task's result and enqueues it on the lane queue so the
brain runs ONE consolidation turn whose reply lands on the original
channel via the configured ``consolidator_factory``.

Cron-fired tasks (``source="cron"``) and standalone user-initiated
tasks (``source="user"``, the default) keep their existing per-task
push behaviour — they're not part of a brain fan-out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from lazyclaw.runtime.team_lead import TeamLead

from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import encrypt, decrypt, is_encrypted
from lazyclaw.db.connection import db_session
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.consolidation_guidance import (
    CONSOLIDATION_TURN_PREFIX,
    COHERENCE_LOG_TAG,
    build_failure_guidance,
    draft_claims_success,
)
from lazyclaw.runtime.consolidator_routing import is_live_web_callback
from lazyclaw.runtime import task_event_bus
from lazyclaw.teams.failure_report import (
    FAILURE_REPORT_MARKER,
    extract_failure_report,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Counter for human-readable task names
_task_counter = 0


# Cap for the mirrored result body — full result stays in the encrypted
# background_tasks.result column; this is just the brain-resident excerpt.
_MIRROR_RESULT_CAP = 4000
_MIRROR_INSTRUCTION_CAP = 800


async def _mirror_background_result(
    config,
    user_id: str,
    *,
    task_id: str,
    task_name: str,
    instruction: str,
    result: str,
    summary,
) -> str | None:
    """Mirror a completed background task into LazyBrain as a research note.

    Tags ``kind/research source/background-task owner/agent`` so the recall
    fan-out picks it up alongside user-saved memories. Importance 5 by
    default (above noise, below explicit user facts). Title carries the
    task name so the user can wikilink back to the source artifact.

    Returns the new note id on success, ``None`` on any failure.
    """
    try:
        from lazyclaw.lazybrain import store as lb_store
    except Exception:
        logger.debug("lazybrain store import failed", exc_info=True)
        return None

    instr = (instruction or "").strip()
    if len(instr) > _MIRROR_INSTRUCTION_CAP:
        instr = instr[: _MIRROR_INSTRUCTION_CAP - 1].rstrip() + "…"
    res = (result or "").strip()
    if len(res) > _MIRROR_RESULT_CAP:
        res = res[: _MIRROR_RESULT_CAP - 1].rstrip() + "…"

    lines: list[str] = [
        f"**Task:** {task_name}",
        f"**Background ID:** `{task_id}`",
        "",
        "### Instruction",
        instr or "(empty)",
        "",
        "### Result",
        res or "(empty)",
    ]
    if summary is not None:
        try:
            stats = (
                f"Tokens: {summary.total_tokens} · "
                f"LLM calls: {summary.llm_calls} · "
                f"Cost: ${summary.total_cost:.4f} · "
                f"Tools: {', '.join(list(summary.tools_used)[:6]) or 'none'}"
            )
            lines.extend(["", "### Stats", stats])
        except Exception:
            pass

    body = "\n".join(lines)
    title = f"Research · {task_name}"
    tags = ["kind/research", "owner/agent", "source/background-task", "auto"]
    frontmatter = {
        "kind": "research",
        "source": "background-task",
        "background_task_id": task_id,
        "task_name": task_name,
    }
    try:
        note = await lb_store.save_note(
            config, user_id,
            content=body,
            title=title,
            tags=tags,
            importance=5,
            frontmatter=frontmatter,
        )
        return note.get("id")
    except Exception:
        logger.debug("save_note failed for background mirror", exc_info=True)
        return None


def _short_name(instruction: str, task_id: str) -> str:
    """Generate a short human-readable name from the instruction.

    Examples: 'Task #1: check bitcoin price', 'Task #2: send email'.
    """
    global _task_counter
    _task_counter += 1

    # Extract first meaningful words (skip [JOB:...] prefixes)
    text = instruction.strip()
    if text.startswith("["):
        bracket_end = text.find("]")
        if bracket_end > 0:
            text = text[bracket_end + 1:].strip()

    # Take first ~40 chars, break at word boundary
    words = text.split()[:8]
    short = " ".join(words)
    if len(short) > 40:
        short = short[:37] + "..."

    return f"Task #{_task_counter}: {short}" if short else f"Task #{_task_counter}"


# Module-level singleton — set in TaskRunner.__init__.
# Skills use this to access the runner without constructor injection.
_task_runner_instance = None  # set to TaskRunner instance at runtime

# Concurrency limits — agents are async coroutines (~50KB each), not processes
MAX_GLOBAL_TASKS = 10
MAX_PER_USER_TASKS = 10
DEFAULT_TIMEOUT = 300  # 5 minutes

# Maximum sub-agent nesting depth. Mirrors Claude Code's per-AgentDefinition
# `maxTurns` bound: the parent agent (depth=0) may spawn background sub-agents
# (depth=1), and those subs MAY themselves fan out one more level (depth=2)
# for legitimate cases like "research finds N matches → apply to each in
# parallel". Beyond that, `submit()` raises RuntimeError so the brain sees a
# clean tool_result instead of looping into a 3-strike failure. Observed
# 2026-05-16: a background `upwork_refresh_check` task spawned an inner
# `run_background` recursively before this guard existed.
MAX_TASK_DEPTH = 2


# Maximum chars per task result included in the synthetic consolidation
# message. Aggressive truncation keeps the synthetic LLM call cheap.
_CONSOLIDATION_RESULT_PREVIEW = 1500


# Re-delegation budget for failed fan-outs: a consolidation turn that was
# offered option (a) "re-delegate to a different specialist" may trigger
# at most ONE follow-up round per originating turn. The follow-up group
# inherits retry_round=1 via _claim_retry_round; its own consolidation
# (if it fails again) forbids further delegation — no specialist
# ping-pong.
_MAX_REDELEGATE_ROUNDS = 1

# How long a granted retry round stays claimable. Generous — covers the
# consolidation turn's LLM latency + the brain actually re-delegating —
# while guaranteeing a stale grant can't tag an unrelated fan-out started
# minutes later.
_RETRY_ROUND_TTL_S = 600.0


# A background worker's final reply longer than this is treated as a real
# synthesis, never as a bare promise — even if it contains a courtesy
# action-claim line. Keeps the RC3 guard from clobbering legitimate answers.
_STRANDED_PROMISE_MAX_LEN = 500


# Narrow, dedicated regex for the stranded-fan-out signature. Deliberately
# NARROWER than agent.py's broad ``_ACTION_CLAIM_RE`` so RC3 never rewrites
# a genuine short answer that merely ends with a courtesy line ("Bitcoin is
# $X. I'll keep you posted." must survive). It matches only forward-looking
# statements ABOUT dispatched work — readers/workers scanning, "I'll have
# your summary shortly", "I'll fold the results into my next reply",
# "results will follow".
_DISPATCH_STATUS_RE = re.compile(
    r"("
    r"\b(?:readers?|workers?|agents?|subagents?)\s+(?:are|is)\s+"
    r"(?:scanning|reading|searching|pulling|checking|fetching|gathering)\b"
    r"|\bscanning\s+(?:your|the|both|whatsapp|email|messages?|inbox|chats?)\b"
    r"|\bi'?ll\s+have\s+(?:your|the|that|a|it|them|both)\b[^.\n]{0,40}?\b"
    r"(?:in\s+a\s+(?:few|moment|sec|couple)|shortly|soon|momentarily|ready)"
    r"|\bi'?ll\s+fold\s+(?:the\s+|these\s+|their\s+)?results?\b"
    r"|\bfold\s+(?:the\s+|their\s+)?results?\s+into\s+my\s+next\b"
    r"|\binto\s+my\s+next\s+(?:reply|turn|message)\b"
    r"|\b(?:results?|summary|answer|update)\s+(?:will\s+)?(?:follow|arrive|land|come\s+(?:back|in))\b"
    r")",
    re.IGNORECASE,
)


def _looks_like_stranded_dispatch_promise(
    result_text: str | None,
    tools_used: list[str] | None,
) -> bool:
    """RC3 guard — True when a background worker dispatched subagents and
    then ended its turn with a SHORT dispatch-status promise instead of a
    synthesized answer.

    The 2026-05-29 "Chek my whats up" bug: an AUTO-PROMOTE'd worker called
    ``dispatch_subagents`` and replied "two readers are scanning WhatsApp
    and Email in parallel. I'll have your summary in a few seconds." with
    no tool calls. That promise was stored as the task result and rendered
    as "✅ Background task completed" — a lie, since the worker produced no
    answer and its subagents stranded (a finished worker has no "next
    reply" to fold results into).

    The guard is deliberately tight to avoid destroying real answers:

      * fires ONLY when ``dispatch_subagents`` is among ``tools_used`` (so
        there genuinely IS async work the promise refers to but this
        finished worker can never fulfill), AND
      * the reply is short (``< _STRANDED_PROMISE_MAX_LEN`` chars — a status
        line, not a synthesis), AND
      * the reply matches the NARROW :data:`_DISPATCH_STATUS_RE` (forward-
        looking statements about the dispatched work — NOT the broad
        action-claim family, so "Bitcoin is $X. I'll keep you posted."
        survives untouched).

    Empty replies are handled by the dedicated empty-reply fallback and
    return False here.
    """
    text = (result_text or "").strip()
    if not text or len(text) > _STRANDED_PROMISE_MAX_LEN:
        return False
    if not any("dispatch_subagents" in (t or "") for t in (tools_used or [])):
        return False
    return bool(_DISPATCH_STATUS_RE.search(text))


@dataclass
class _FanoutResult:
    """One settled task within a brain fan-out group."""

    task_id: str
    name: str
    success: bool
    result: str = ""
    error: str = ""
    duration_ms: int | None = None


@dataclass
class _BrainFanoutGroup:
    """In-flight brain-initiated fan-out: sibling tasks spawned in the same
    agent TAOR turn, consolidated once the last sibling settles.

    Mutable on purpose — the runner adds tasks as ``submit`` is called and
    moves them from ``pending`` to ``results`` as ``_execute`` settles them.
    """

    group_id: str
    user_id: str
    pending: set[str] = field(default_factory=set)
    results: list[_FanoutResult] = field(default_factory=list)
    consolidator_cb: object | None = None  # AgentCallback or None
    chat_session_id: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    # 0 = original fan-out; 1 = follow-up spawned after a failure-guided
    # consolidation turn re-delegated. At _MAX_REDELEGATE_ROUNDS the
    # guidance forbids further delegation (see _claim_retry_round).
    retry_round: int = 0


class TaskRunner:
    """Runs agent tasks in background, parallel to foreground chat.

    Usage:
        runner = TaskRunner(config, router, registry, eco_router)
        task_id = await runner.submit(user_id, "check bitcoin price", name="btc")
        # Returns immediately — task runs in background
        # User notified via callback when done
    """

    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry,
        eco_router: EcoRouter,
        permission_checker=None,
        default_callback: AgentCallback | None = None,
        team_lead: TeamLead | None = None,
        lane_queue=None,
        consolidator_factory: Callable | None = None,
    ) -> None:
        self._config = config
        self._router = router
        self._registry = registry
        self._eco_router = eco_router
        self._permission_checker = permission_checker
        self._default_callback = default_callback
        self._team_lead = team_lead
        # Lane queue is used to enqueue the synthetic consolidation turn
        # when a brain fan-out group settles. Without it we degrade to
        # the legacy per-task push path so behaviour is unchanged.
        self._lane_queue = lane_queue
        # Factory for the consolidation reply's callback. Receives
        # (user_id, original_callback) and returns an AgentCallback that
        # delivers the brain's reply to whatever channel the original
        # turn was on (Telegram with a "🧠 Consolidated" prefix, etc.).
        # Falls back to the original callback when not configured.
        self._consolidator_factory = consolidator_factory

        # Set module-level singleton for skill access
        global _task_runner_instance
        _task_runner_instance = self

        # In-memory tracking (cleaned up on completion)
        self._running: dict[str, asyncio.Task] = {}
        self._task_users: dict[str, str] = {}
        self._task_names: dict[str, str] = {}
        self._task_starts: dict[str, float] = {}
        # task_id → (source, fanout_group_id) so _execute knows whether
        # to suppress the per-task push and route into a fan-out group.
        self._task_provenance: dict[str, tuple[str, str | None]] = {}
        # task_id → caller depth. _execute reads this and constructs the
        # inner Agent with depth=caller_depth+1, so nested submit() calls
        # from inside a background task can be bounded at MAX_TASK_DEPTH.
        # Without this map the inner Agent had depth=0 and could spawn
        # forever before falling back to the three-strikes handoff.
        self._task_caller_depth: dict[str, int] = {}
        # task_id → per-task workspace directory. submit() creates the
        # dir before spawning _execute; _execute reads it back for the
        # post-run file glob. Cleaned up alongside other task maps.
        self._task_workspace_dirs: dict[str, str] = {}
        # group_id → _BrainFanoutGroup. Cleaned up after _consolidate runs.
        self._brain_groups: dict[str, _BrainFanoutGroup] = {}
        # user_id → (next_retry_round, granted_at). Set when a failed
        # consolidation OFFERS re-delegation; claimed (popped) by the
        # next fan-out group created for that user so the follow-up
        # round inherits the budget counter. TTL'd in _claim_retry_round.
        self._fanout_retry_rounds: dict[str, tuple[int, float]] = {}

    async def submit(
        self,
        user_id: str,
        instruction: str,
        name: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        callback: AgentCallback | None = None,
        on_complete=None,
        source: str = "user",
        fanout_group_id: str | None = None,
        chat_session_id: str | None = None,
        project_tag: str = "",
        caller_depth: int = 0,
        goal_id: str = "",
    ) -> str:
        """Submit a task for background execution. Returns task_id immediately.

        Raises RuntimeError if concurrency limits exceeded.

        ``source`` distinguishes how the task got here:

        - ``"brain"`` — spawned by an agent TAOR turn (run_background tool).
          Per-task push is suppressed; results are routed into a fan-out
          group keyed by ``fanout_group_id`` and the brain consolidates
          once every sibling has settled.
        - ``"cron"`` — heartbeat-fired scheduled job / reminder / watcher.
          Keeps existing per-task push behaviour.
        - ``"user"`` (default) — standalone task. Keeps existing
          per-task push behaviour.
        """
        # Validate limits
        if caller_depth >= MAX_TASK_DEPTH:
            raise RuntimeError(
                f"Max background nesting depth ({MAX_TASK_DEPTH}) reached. "
                f"This task is already running inside another background task "
                f"at depth {caller_depth} — do the work inline instead of "
                f"spawning a third level."
            )
        if len(self._running) >= MAX_GLOBAL_TASKS:
            raise RuntimeError(
                f"Maximum {MAX_GLOBAL_TASKS} background tasks running globally. "
                f"Wait for one to finish or cancel with /tasks."
            )
        user_count = sum(1 for u in self._task_users.values() if u == user_id)
        if user_count >= MAX_PER_USER_TASKS:
            raise RuntimeError(
                f"Maximum {MAX_PER_USER_TASKS} background tasks per user. "
                f"Wait for one to finish or cancel with /tasks."
            )

        task_id = str(uuid4())
        task_name = name or _short_name(instruction, task_id)

        # Store in DB (encrypted)
        key = await get_user_dek(self._config, user_id)
        encrypted_instruction = encrypt(instruction, key)

        # Pre-compute short_description for the Code Specialist Web UI.
        # First line of the instruction (≤120 chars). Pre-stored so the
        # Web UI has it even while the task is still running, before any
        # capture path fires. Plain text — derived from already-encrypted
        # instruction so no encryption needed.
        _short_desc = (instruction.strip().splitlines()[0][:120]
                       if instruction else task_name)

        # Per-task workspace dir. claude-code MCP runs with cwd=/workspace
        # (mcp/manager.py), so even bg tasks that invoke claude-code via
        # the brain's run_background path land their files under a
        # per-task subdir — letting the Code Specialist Web UI surface a
        # clickable folder path. Best-effort mkdir; failure (e.g. RO host
        # mount) is logged and we record an empty path so the UI degrades
        # gracefully.
        from lazyclaw.teams.specialist import code_workspace_dir
        _workspace_dir = code_workspace_dir(
            task_id=task_id,
            project_tag=project_tag,
            goal_id=goal_id,
        )
        try:
            import os as _os
            _os.makedirs(_workspace_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "bg task workspace mkdir failed for %s: %s — "
                "task will run without per-task workspace isolation",
                _workspace_dir, exc,
            )
            _workspace_dir = ""

        async with db_session(self._config) as db:
            await db.execute(
                "INSERT INTO background_tasks "
                "(id, user_id, name, instruction, status, timeout, "
                "short_description, goal_id, workspace_dir) "
                "VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                (task_id, user_id, task_name, encrypted_instruction, timeout,
                 _short_desc, goal_id or None, _workspace_dir or None),
            )
            await db.commit()

        # Spawn background execution
        bg_task = asyncio.create_task(
            self._execute(task_id, user_id, instruction, timeout, callback, on_complete),
            name=f"bg-{task_name}",
        )
        self._running[task_id] = bg_task
        self._task_users[task_id] = user_id
        self._task_names[task_id] = task_name
        self._task_workspace_dirs[task_id] = _workspace_dir or ""
        self._task_starts[task_id] = time.monotonic()
        self._task_provenance[task_id] = (source, fanout_group_id)
        self._task_caller_depth[task_id] = caller_depth

        # Brain fan-out: bucket sibling tasks under a shared group so we
        # can fire ONE consolidation turn when the last one settles,
        # instead of N "✅ done" pushes the user has to read individually.
        if source == "brain" and fanout_group_id:
            group = self._brain_groups.get(fanout_group_id)
            if group is None:
                group = _BrainFanoutGroup(
                    group_id=fanout_group_id,
                    user_id=user_id,
                    consolidator_cb=callback,
                    chat_session_id=chat_session_id,
                    retry_round=self._claim_retry_round(user_id),
                )
                self._brain_groups[fanout_group_id] = group
                logger.info(
                    "Brain fan-out group %s created (user=%s)",
                    fanout_group_id, user_id,
                )
            group.pending.add(task_id)
            logger.info(
                "Brain fan-out group %s registered task=%s (pending=%d)",
                fanout_group_id, task_id[:8], len(group.pending),
            )

        # Register with TeamLead for instant status
        if self._team_lead:
            self._team_lead.register(
                task_id, task_name, instruction[:80], "background",
                instruction_full=instruction,
                user_id=user_id,
                project_tag=project_tag,
                goal_id=goal_id,
            )

        # Announce on the per-user task event bus so a connected web chat
        # paints the new background card the same instant the task starts —
        # without waiting for the next 3 s /api/agents/status poll.
        try:
            task_event_bus.publish(task_event_bus.TaskEvent(
                user_id=user_id,
                kind="background_started",
                task_id=task_id,
                name=task_name,
                lane="background",
                description=instruction[:80],
                source=source,
                fanout_group_id=fanout_group_id,
            ))
        except Exception:
            logger.debug("task_event_bus publish (started) failed", exc_info=True)

        logger.info(
            "Background task %s (%s) started for user %s",
            task_id[:8], task_name, user_id,
        )
        return task_id

    async def _execute(
        self,
        task_id: str,
        user_id: str,
        instruction: str,
        timeout: int,
        callback: AgentCallback | None,
        on_complete=None,
    ) -> None:
        """Run agent in background with its own context."""
        from lazyclaw.runtime.agent import Agent
        from lazyclaw.runtime.events import WorkSummary

        # Fall back to default notifier so background tasks ALWAYS notify
        callback = callback or self._default_callback
        key = await get_user_dek(self._config, user_id)
        task_name = self._task_names.get(task_id, task_id[:8])
        _status = "done"
        _source, _group_id = self._task_provenance.get(task_id, ("user", None))
        _is_brain_fanout = _source == "brain" and _group_id is not None

        # Wrapper callback to capture work_summary AND drive TeamLead step
        # updates so the Activity/Overview UI shows the bg agent's current
        # tool live, not only on completion.
        _captured_summary: WorkSummary | None = None
        _original_cb = callback
        _team_lead_ref = self._team_lead
        _bound_task_id = task_id

        # Capture a stable display name for the bg task so chat-side
        # consumers can attach a label ("Background: salon_email_research")
        # to the live progress card.
        _bg_task_display_name = task_name

        # Per-step transcript accumulator. The CodeSpecialist Web UI
        # renders this as a step-by-step timeline so the user can see
        # every claude-code MCP call (and any other tool) the bg agent
        # made. We capture tool_call → start (name + args), tool_result
        # → finish (result preview + duration). Capped at _TS_CAP rows so
        # a runaway loop can't bloat the DB row. Each entry is a small
        # dict — encoded to JSON before encrypting on completion.
        _TS_CAP = 200
        _ts_steps: list[dict] = []
        _ts_inflight: dict[str, dict] = {}  # tool_call_id -> partial step

        class _BgEventTap:
            """Transparent wrapper around the user's original callback.

            On `work_summary` it captures the WorkSummary so the runner
            can persist cost/token stats. On `tool_call` it pings TeamLead
            so the running background task's `current_tool` / `recent_tools`
            stay live for the dashboard poll AND for the live event bus
            (TeamLead._publish → task_event_bus → chat WS).

            Also tags every forwarded event's metadata with ``bg_task_id``
            and ``bg_task_name`` so chat WS can demux foreground vs
            background streaming and render the bg task's progress as a
            separate "Background: <name>" card instead of appending to the
            (already-completed) foreground turn's tool list.
            Fixed 2026-04-29 after a 39-iter foreground grind made the bg
            tool stream invisible to the user.
            """

            def __getattr__(self, name):
                return getattr(_original_cb, name)

            async def on_event(self, event: AgentEvent) -> None:
                nonlocal _captured_summary
                if event.kind == "work_summary":
                    _captured_summary = event.metadata.get("summary")
                elif event.kind == "tool_call":
                    md = event.metadata or {}
                    tool_name = (
                        md.get("display_name")
                        or md.get("tool")
                        or event.detail
                    )
                    if _team_lead_ref is not None:
                        try:
                            _team_lead_ref.update_step(_bound_task_id, str(tool_name))
                        except Exception:
                            logger.debug(
                                "team_lead.update_step failed for bg task %s",
                                _bound_task_id, exc_info=True,
                            )
                    # Begin transcript step row. CodeSpecialist.tsx
                    # renders this as the step timeline. Cap arg preview
                    # at 120 chars to keep payload tight.
                    if len(_ts_steps) < _TS_CAP:
                        tc_id = md.get("tool_call_id") or md.get("id") or ""
                        args = md.get("arguments") or md.get("args") or {}
                        try:
                            args_text = (
                                args if isinstance(args, str)
                                else json.dumps(args, default=str)
                            )
                        except Exception:
                            args_text = str(args)
                        partial = {
                            "kind": "tool",
                            "name": str(tool_name),
                            "args_summary": " ".join(args_text.split())[:120],
                            "result_summary": "",
                            "duration_ms": 0,
                            "success": True,
                            "error": "",
                            "_started_at": time.monotonic(),
                        }
                        _ts_steps.append(partial)
                        if tc_id:
                            _ts_inflight[tc_id] = partial
                elif event.kind == "tool_result":
                    md = event.metadata or {}
                    tc_id = md.get("tool_call_id") or md.get("id") or ""
                    target = _ts_inflight.pop(tc_id, None)
                    if target is None and _ts_steps:
                        # Fallback: pair to most-recent inflight when
                        # provider didn't carry tool_call_id. Reasonable
                        # because tool calls are sequential per turn.
                        target = _ts_steps[-1]
                    if target is not None:
                        result_text = (
                            md.get("preview") or md.get("result") or event.detail or ""
                        )
                        try:
                            result_text = (
                                result_text if isinstance(result_text, str)
                                else json.dumps(result_text, default=str)
                            )
                        except Exception:
                            result_text = str(result_text)
                        target["result_summary"] = " ".join(result_text.split())[:200]
                        started = target.pop("_started_at", time.monotonic())
                        target["duration_ms"] = int((time.monotonic() - started) * 1000)
                        if isinstance(result_text, str) and result_text.startswith("Error"):
                            target["success"] = False
                            target["error"] = result_text[:200]

                # Tag the event with bg-task identity so chat WS demuxes
                # bg streams from the active foreground turn. Mutating
                # metadata in-place is fine — events are not retained
                # after dispatch. We still forward the (now-tagged) event
                # to the original callback below.
                try:
                    if not isinstance(event.metadata, dict):
                        event.metadata = {}
                    event.metadata.setdefault("bg_task_id", _bound_task_id)
                    event.metadata.setdefault("bg_task_name", _bg_task_display_name)
                except Exception:
                    logger.debug("bg event tagging failed", exc_info=True)

                await _original_cb.on_event(event)

        callback = _BgEventTap()

        try:
            # Create FRESH Agent instance (isolated state, no race conditions).
            #
            # Thread `task_runner`, `team_lead`, and incremented `depth` into
            # the sub-agent so legitimate nested fan-out works (e.g. research
            # bg → apply-to-each parallel bgs). Mirrors Claude Code's
            # AgentDefinition pattern where a sub-agent's `tools` list may
            # include `Task` and recursion is bounded by per-agent `maxTurns`.
            # Without this wiring the inner Agent had _task_runner=None and
            # any nested run_background call returned "background task runner
            # not configured" three times in a row before the three-strikes
            # handoff fired — clean error semantics now via MAX_TASK_DEPTH.
            _caller_depth = self._task_caller_depth.get(task_id, 0)
            agent = Agent(
                config=self._config,
                router=self._router,
                registry=self._registry,
                eco_router=self._eco_router,
                permission_checker=self._permission_checker,
                task_runner=self,
                team_lead=self._team_lead,
                depth=_caller_depth + 1,
            )
            agent.is_background = True  # Browser uses headless in background

            async with asyncio.timeout(timeout):
                result = await agent.process_message(
                    user_id, instruction, callback=callback,
                )

            # Empty-reply fallback: when the brain's final LLM call returns
            # ``content_len=0`` (no synthesis text), the user otherwise sees
            # only a generic "Task completed" with no actionable info.
            # Build a one-shot summary from tools_used so the user at least
            # knows what ran. Real diagnosis still lives in the logs, but
            # this turns "??? task completed" into something readable.
            if not (result or "").strip() and _captured_summary is not None:
                tools = list(_captured_summary.tools_used or [])
                duration_s = (_captured_summary.duration_ms or 0) // 1000
                if tools:
                    tool_lines = "\n".join(f"  • {t}" for t in tools[:10])
                    if len(tools) > 10:
                        tool_lines += f"\n  • … +{len(tools) - 10} more"
                    result = (
                        f"⚠️ Background task ran but returned no synthesis text "
                        f"(brain LLM had nothing left to say after the tool calls).\n\n"
                        f"Tools called ({len(tools)}, {duration_s}s):\n{tool_lines}\n\n"
                        f"If this was an Upwork apply that didn't submit, ask me "
                        f"to retry — the get_proposals default-status fix in 5/10's "
                        f"patches should now keep the brain unblocked."
                    )
                else:
                    result = (
                        f"⚠️ Background task ran for {duration_s}s but produced no "
                        f"text and called no tools. Likely brain stalled — please retry."
                    )

            # RC3 — a worker that dispatched subagents and then only
            # PROMISED ("two readers are scanning… I'll have your summary in
            # a few seconds") must NOT store that promise as a "✅ completed"
            # result. A finished worker has no next turn to fulfill it, so
            # its subagents strand. Rewrite to honest status so the
            # completion card never impersonates a delivered answer.
            # (2026-05-29 "Chek my whats up" incident.)
            _tools_used = (
                list(_captured_summary.tools_used or [])
                if _captured_summary else []
            )
            if _looks_like_stranded_dispatch_promise(result, _tools_used):
                logger.warning(
                    "Background task %s stored a stranded dispatch promise "
                    "(%.80r) — rewriting to honest status",
                    task_id[:8], result,
                )
                result = (
                    "⏳ This task dispatched background subagents and then "
                    "replied with a status only — it produced no synthesized "
                    "answer of its own. If the subagents return useful data it "
                    "will arrive in a separate consolidated reply; if nothing "
                    "follows shortly, ask me to retry inline.\n\n"
                    f"(Worker's interim status: {result.strip()[:200]})"
                )

            # Store result (encrypted) + cost stats from work_summary
            encrypted_result = encrypt(result, key)
            _cost = _captured_summary.total_cost if _captured_summary else 0.0
            _tokens = _captured_summary.total_tokens if _captured_summary else 0
            _calls = _captured_summary.llm_calls if _captured_summary else 0

            # ── Code Specialist visibility (bg task path) ──────────
            # Strip transient bookkeeping fields before persisting; only
            # the public-shape rows (kind/name/args/result/duration/etc.)
            # ride into the encrypted column.
            _public_steps = [
                {k: v for k, v in s.items() if not k.startswith("_")}
                for s in _ts_steps
            ]
            _ts_blob = (
                encrypt(json.dumps(_public_steps, default=str), key)
                if _public_steps else None
            )
            _prompt_blob = encrypt(instruction, key) if instruction else None
            # Glob the workspace dir post-run so users get a clickable
            # file list. Skip-list mirrors teams/runner.py to keep the
            # bg task path and the specialist path consistent.
            _files_touched: list[str] = []
            # _workspace_dir was created in submit() and stashed on
            # self._task_workspace_dirs — fetch it here. Defaults to ""
            # (no glob) when submit's mkdir failed or the entry was
            # already cleaned up.
            _workspace_dir = self._task_workspace_dirs.get(task_id, "")
            if _workspace_dir:
                try:
                    import os as _os
                    _skip = {".git", "__pycache__", "node_modules", ".venv", "venv"}
                    for _root, _dirs, _fs in _os.walk(_workspace_dir):
                        _dirs[:] = [
                            d for d in _dirs
                            if d not in _skip and not d.startswith(".")
                        ]
                        for _f in _fs:
                            if _f.startswith("."):
                                continue
                            _files_touched.append(
                                _os.path.relpath(_os.path.join(_root, _f),
                                                 _workspace_dir)
                            )
                            if len(_files_touched) >= 64:
                                break
                        if len(_files_touched) >= 64:
                            break
                    _files_touched.sort()
                except OSError:
                    logger.debug("workspace glob failed", exc_info=True)
            _files_blob = (
                encrypt(json.dumps(_files_touched), key)
                if _files_touched else None
            )

            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'done', result = ?, "
                    "cost_usd = ?, tokens_used = ?, llm_calls = ?, "
                    "mcp_prompt = ?, mcp_transcript = ?, files_touched = ?, "
                    "completed_at = datetime('now') WHERE id = ?",
                    (encrypted_result, _cost, _tokens, _calls,
                     _prompt_blob, _ts_blob, _files_blob, task_id),
                )
                await db.commit()

            # Mirror the result into LazyBrain so it's RAG-searchable.
            # Without this, every run_background output is lost the moment
            # the task settles — the user can't ask "what did that research
            # find?" two hours later. PKM mirror failures never fail the
            # task itself (Ollama down, encryption error, etc.).
            try:
                lb_note_id = await _mirror_background_result(
                    self._config, user_id,
                    task_id=task_id,
                    task_name=task_name,
                    instruction=instruction,
                    result=result,
                    summary=_captured_summary,
                )
                if lb_note_id:
                    async with db_session(self._config) as db:
                        await db.execute(
                            "UPDATE background_tasks SET lazybrain_note_id = ? "
                            "WHERE id = ?",
                            (lb_note_id, task_id),
                        )
                        await db.commit()
            except Exception:
                logger.debug(
                    "background → lazybrain mirror failed for %s",
                    task_id[:8], exc_info=True,
                )

            logger.info("Background task %s (%s) completed", task_id[:8], task_name)

            if self._team_lead:
                self._team_lead.complete(
                    task_id, result[:100], result_full=result,
                )

            # Build rich notification with stats from work_summary
            meta: dict = {"task_id": task_id, "name": task_name, "result": result}
            if _captured_summary is not None:
                meta["duration_ms"] = _captured_summary.duration_ms
                meta["total_tokens"] = _captured_summary.total_tokens
                meta["llm_calls"] = _captured_summary.llm_calls
                meta["tools_used"] = list(_captured_summary.tools_used)
                meta["total_cost"] = _captured_summary.total_cost
                meta["models_used"] = [m[0] for m in _captured_summary.models_used]

            # Brain fan-out: suppress the per-task push and route the
            # result into the group; consolidation will fire ONE reply
            # when the last sibling settles. Cron / user-source tasks
            # keep their existing per-task push.
            if _is_brain_fanout:
                meta["source"] = "brain"
                meta["fanout_group_id"] = _group_id
                self._record_brain_result(_FanoutResult(
                    task_id=task_id, name=task_name, success=True,
                    result=result or "",
                    duration_ms=meta.get("duration_ms"),
                ))
            elif _original_cb:
                # Notify user (Telegram + dashboard via the callback),
                # AND fan-in to the per-user task event bus so the web
                # chat WebSocket can surface the result inline.
                await _original_cb.on_event(AgentEvent(
                    "background_done",
                    f"Background task '{task_name}' completed",
                    meta,
                ))
            try:
                task_event_bus.publish(task_event_bus.TaskEvent(
                    user_id=user_id,
                    kind="background_done",
                    task_id=task_id,
                    name=task_name,
                    result=(result or "")[:4000],
                    duration_ms=meta.get("duration_ms"),
                    total_tokens=meta.get("total_tokens"),
                    llm_calls=meta.get("llm_calls"),
                    total_cost=meta.get("total_cost"),
                    tools_used=tuple(meta.get("tools_used", []) or []),
                    source=_source,
                    fanout_group_id=_group_id,
                ))
            except Exception:
                logger.debug("task_event_bus publish (done) failed", exc_info=True)

        except asyncio.TimeoutError:
            _status = "failed"
            if self._team_lead:
                self._team_lead.fail(task_id, f"Timed out after {timeout}s")
            logger.warning(
                "Background task %s (%s) timed out after %ds",
                task_id[:8], task_name, timeout,
            )
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'failed', "
                    "error = ?, completed_at = datetime('now') WHERE id = ?",
                    (f"Timed out after {timeout} seconds", task_id),
                )
                await db.commit()

            if _is_brain_fanout:
                self._record_brain_result(_FanoutResult(
                    task_id=task_id, name=task_name, success=False,
                    error=f"Timed out after {timeout}s",
                ))
            elif callback:
                await callback.on_event(AgentEvent(
                    "background_failed",
                    f"Background task '{task_name}' timed out",
                    {"task_id": task_id, "name": task_name,
                     "error": f"Timed out after {timeout}s",
                     "source": _source, "fanout_group_id": _group_id},
                ))
            try:
                task_event_bus.publish(task_event_bus.TaskEvent(
                    user_id=user_id,
                    kind="background_failed",
                    task_id=task_id,
                    name=task_name,
                    error=f"Timed out after {timeout}s",
                    source=_source,
                    fanout_group_id=_group_id,
                ))
            except Exception:
                logger.debug("task_event_bus publish (timeout) failed", exc_info=True)

        except asyncio.CancelledError:
            _status = "cancelled"
            if self._team_lead:
                self._team_lead.cancel(task_id)
            logger.info("Background task %s (%s) cancelled", task_id[:8], task_name)
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'cancelled', "
                    "completed_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()
            # If this was a brain fan-out task, record a cancelled
            # outcome so the group can settle and consolidation fires
            # for its surviving siblings (otherwise the group would
            # leak).
            if _is_brain_fanout:
                self._record_brain_result(_FanoutResult(
                    task_id=task_id, name=task_name, success=False,
                    error="Cancelled by user",
                ))
            elif _original_cb:
                # Mirror the timeout / failure paths so the user actually
                # sees that their cancel landed. Without this, cancelling
                # a single bg task left the chat UI in "running…" forever.
                try:
                    await _original_cb.on_event(AgentEvent(
                        "background_failed",
                        f"Background task '{task_name}' cancelled",
                        {"task_id": task_id, "name": task_name,
                         "error": "Cancelled by user",
                         "source": _source, "fanout_group_id": _group_id},
                    ))
                except Exception:
                    logger.debug(
                        "callback push (cancel) failed for %s",
                        task_id[:8], exc_info=True,
                    )
            try:
                task_event_bus.publish(task_event_bus.TaskEvent(
                    user_id=user_id,
                    kind="background_failed",
                    task_id=task_id,
                    name=task_name,
                    error="Cancelled by user",
                    source=_source,
                    fanout_group_id=_group_id,
                ))
            except Exception:
                logger.debug("task_event_bus publish (cancel) failed", exc_info=True)

        except Exception as exc:
            _status = "failed"
            if self._team_lead:
                self._team_lead.fail(task_id, str(exc)[:200])
            logger.error(
                "Background task %s (%s) failed: %s",
                task_id[:8], task_name, exc,
            )
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'failed', "
                    "error = ?, completed_at = datetime('now') WHERE id = ?",
                    (str(exc)[:500], task_id),
                )
                await db.commit()

            if _is_brain_fanout:
                self._record_brain_result(_FanoutResult(
                    task_id=task_id, name=task_name, success=False,
                    error=str(exc)[:500],
                ))
            elif callback:
                await callback.on_event(AgentEvent(
                    "background_failed",
                    f"Background task '{task_name}' failed",
                    {"task_id": task_id, "name": task_name,
                     "error": str(exc)[:200],
                     "source": _source, "fanout_group_id": _group_id},
                ))
            try:
                task_event_bus.publish(task_event_bus.TaskEvent(
                    user_id=user_id,
                    kind="background_failed",
                    task_id=task_id,
                    name=task_name,
                    error=str(exc)[:500],
                    source=_source,
                    fanout_group_id=_group_id,
                ))
            except Exception:
                logger.debug("task_event_bus publish (fail) failed", exc_info=True)

        finally:
            # ALWAYS clean up (prevents memory leaks)
            self._running.pop(task_id, None)
            self._task_users.pop(task_id, None)
            self._task_names.pop(task_id, None)
            self._task_starts.pop(task_id, None)
            self._task_provenance.pop(task_id, None)
            self._task_caller_depth.pop(task_id, None)
            self._task_workspace_dirs.pop(task_id, None)

            # Notify originator (e.g., team lead state cleanup)
            if on_complete:
                try:
                    await on_complete(task_id, _status)
                except Exception as exc:
                    logger.warning("on_complete callback failed for task %s: %s", task_id[:8], exc)

    # ── Brain fan-out consolidation ──────────────────────────────────

    def register_subagent_fanout(
        self,
        group_id: str,
        user_id: str,
        task_ids: list[str],
        callback: "AgentCallback | None",
        chat_session_id: str | None = None,
    ) -> bool:
        """RC2 — register a ``dispatch_subagents`` fan-out so its results
        consolidate into ONE brain reply, reusing the exact brain-fan-out
        machinery ``run_background`` uses (``_record_brain_result`` →
        ``_consolidate`` → ``lane_queue.enqueue``).

        Returns ``False`` (no-op) when no lane queue is wired, so the caller
        can keep the legacy fire-and-forget behaviour (results drain via
        ``pending_subagent_notes`` on the next user turn). MUST be called
        BEFORE the subagents are spawned so the group's ``pending`` set is
        populated before any sibling can settle (race-free).
        """
        if self._lane_queue is None or not task_ids:
            return False
        group = self._brain_groups.get(group_id)
        if group is None:
            group = _BrainFanoutGroup(
                group_id=group_id,
                user_id=user_id,
                consolidator_cb=callback,
                chat_session_id=chat_session_id,
                retry_round=self._claim_retry_round(user_id),
            )
            self._brain_groups[group_id] = group
        group.pending.update(task_ids)
        logger.info(
            "Subagent fan-out group %s registered %d task(s) (user=%s)",
            group_id, len(task_ids), user_id,
        )
        return True

    def record_subagent_result(
        self,
        task_id: str,
        name: str,
        success: bool,
        result: str = "",
        error: str = "",
        duration_ms: int | None = None,
    ) -> None:
        """RC2 — feed a settled ``dispatch_subagents`` result into its
        fan-out group. When the last sibling settles, the existing
        ``_consolidate`` fires ONE synthetic brain turn. No-op when the
        task isn't part of a registered group (legacy / unregistered path).
        """
        self._record_brain_result(_FanoutResult(
            task_id=task_id,
            name=name,
            success=success,
            result=result or "",
            error=error or "",
            duration_ms=duration_ms,
        ))

    def _claim_retry_round(self, user_id: str) -> int:
        """Pop the pending retry-round grant for ``user_id`` (0 if none).

        Called when a NEW fan-out group is created: if the previous
        consolidation for this user offered re-delegation (and the grant
        hasn't gone stale), the new group inherits the incremented round
        so its own consolidation can enforce the budget. Defensive
        ``getattr`` keeps legacy ``__new__``-style constructions (tests,
        partially-initialized runners) at round 0 instead of raising.
        """
        rounds = getattr(self, "_fanout_retry_rounds", None)
        if not rounds:
            return 0
        entry = rounds.pop(user_id, None)
        if entry is None:
            return 0
        round_no, granted_at = entry
        if time.monotonic() - granted_at > _RETRY_ROUND_TTL_S:
            logger.debug(
                "retry-round grant for user %s expired — starting at 0",
                user_id,
            )
            return 0
        return round_no

    def _grant_retry_round(self, user_id: str, next_round: int) -> None:
        """Record that the next fan-out for ``user_id`` is a retry round."""
        if getattr(self, "_fanout_retry_rounds", None) is None:
            self._fanout_retry_rounds = {}
        self._fanout_retry_rounds[user_id] = (next_round, time.monotonic())

    def _record_brain_result(self, outcome: _FanoutResult) -> None:
        """Append a settled brain-fan-out task to its group; if this was
        the last sibling, schedule the consolidation turn."""
        # Find which group owns this task. We don't carry the group id on
        # the outcome (the caller would have to plumb it through three
        # branches) — a linear scan over self._brain_groups is cheap
        # because there are at most a handful of in-flight groups.
        target_group: _BrainFanoutGroup | None = None
        for g in self._brain_groups.values():
            if outcome.task_id in g.pending:
                target_group = g
                break
        if target_group is None:
            logger.debug(
                "_record_brain_result: no group owns task %s",
                outcome.task_id,
            )
            return
        target_group.pending.discard(outcome.task_id)
        target_group.results.append(outcome)
        logger.info(
            "Brain fan-out %s: task %s settled (success=%s, pending=%d, total=%d)",
            target_group.group_id, outcome.task_id[:8], outcome.success,
            len(target_group.pending), len(target_group.results),
        )
        if not target_group.pending:
            # Last sibling — schedule consolidation as a fire-and-forget
            # task so the calling _execute can finish its finally block
            # before the new lane-queue turn starts.
            asyncio.create_task(
                self._consolidate(target_group.group_id),
                name=f"brain-consolidate-{target_group.group_id[:8]}",
            )

    async def _consolidate(self, group_id: str) -> None:
        """Build the synthetic consolidation message and enqueue ONE
        brain turn whose reply lands on the original channel."""
        group = self._brain_groups.pop(group_id, None)
        if group is None:
            return

        # 1-result groups: behavior depends on the user's bg_streaming
        # toggle. With streaming ON (default), fall back to the legacy
        # per-task push so single run_background calls keep their
        # immediate "✅ done" UX. With streaming OFF (quiet mode), route
        # through the same synthesizing brain turn as multi-result fan-
        # outs — the user explicitly asked for brain-only voice, so a
        # raw result card would break the contract.
        _quiet = False
        try:
            from lazyclaw.runtime.streaming_setting import get_bg_streaming
            _quiet = not await get_bg_streaming(self._config, group.user_id)
        except Exception:
            logger.debug("bg_streaming lookup failed in _consolidate", exc_info=True)

        # BUG-1 note: this 1-result streaming branch fires ONE
        # ``background_done`` card and returns early — it does NOT run a
        # lane-queue synthetic turn, so it never streams ``token`` frames
        # into a live chat bubble. No spinning bubble is mounted here, so
        # no terminal ``done`` frame is needed (the streaming-ON terminal
        # below only guards the multi-result synthetic-turn path).
        if len(group.results) == 1 and not _quiet:
            r = group.results[0]
            cb = group.consolidator_cb
            if cb is None:
                return
            kind = "background_done" if r.success else "background_failed"
            meta: dict = {
                "task_id": r.task_id, "name": r.name,
                "duration_ms": r.duration_ms,
            }
            if r.success:
                meta["result"] = r.result
            else:
                meta["error"] = r.error
            try:
                await cb.on_event(AgentEvent(
                    kind, f"Background task '{r.name}' "
                    f"{'completed' if r.success else 'failed'}", meta,
                ))
            except Exception:
                logger.debug(
                    "consolidator fallback fire failed for group %s",
                    group_id, exc_info=True,
                )
            return

        # Real fan-out: build a synthetic instruction the brain can fold
        # into ONE consolidated reply. Every result is truncated so the
        # synthetic prompt stays cheap.
        #
        # Failure-shaped groups (any failed sibling, or a structured
        # [SPECIALIST FAILURE REPORT] embedded in a result) additionally
        # get orchestrator guidance appended so the brain DECIDES the
        # next move — re-delegate ONCE / answer from partials / report
        # the blocker — instead of shipping a false "✅ done" or a vague
        # apology (2026-06-10 freelance_specialist stuck-loop incident).
        def _carries_report(r: _FanoutResult) -> bool:
            return (
                FAILURE_REPORT_MARKER in (r.result or "")
                or FAILURE_REPORT_MARKER in (r.error or "")
            )

        _failure_present = any(
            not r.success or _carries_report(r) for r in group.results
        )
        _any_succeeded = any(r.success for r in group.results)

        lines = [
            f"{CONSOLIDATION_TURN_PREFIX} — {len(group.results)} tasks finished]",
            "",
            "Results from background tasks you spawned earlier:",
            "",
        ]
        for i, r in enumerate(group.results, 1):
            header = f"## Task {i}: {r.name}"
            if r.duration_ms:
                header += f" ({r.duration_ms // 1000}s)"
            lines.append(header)
            if r.success:
                preview = (r.result or "")[:_CONSOLIDATION_RESULT_PREVIEW]
                if len(r.result or "") > _CONSOLIDATION_RESULT_PREVIEW:
                    preview += "\n[... truncated]"
                # An all_tools_failed report rides at the END of a
                # success-shaped result — re-attach it if the preview
                # cap chopped it off (silent truncation of structured
                # data is THE failure class of 2026-05-25).
                if (
                    _carries_report(r)
                    and FAILURE_REPORT_MARKER not in preview
                ):
                    preview += "\n" + extract_failure_report(r.result)
                lines.append(preview or "(empty)")
            else:
                lines.append(f"FAILED: {r.error or 'unknown error'}")
                # Surface the structured report (the legacy line above
                # only carries the short error string).
                report = extract_failure_report(r.result)
                if report:
                    lines.append(report[:_CONSOLIDATION_RESULT_PREVIEW])
            lines.append("")
        lines.extend([
            "Write ONE consolidated summary for the user. Don't repeat "
            "raw blobs — synthesize. Call out any failures explicitly. "
            "Keep it tight (~6-12 lines for Telegram).",
        ])

        if _failure_present:
            _can_redelegate = group.retry_round < _MAX_REDELEGATE_ROUNDS
            lines.extend(["", build_failure_guidance(
                can_redelegate=_can_redelegate,
            )])
            if _can_redelegate:
                # The consolidation turn may re-delegate: the next
                # fan-out group this user spawns inherits round+1 so its
                # own consolidation enforces the 1-retry budget.
                self._grant_retry_round(group.user_id, group.retry_round + 1)
        synthetic_msg = "\n".join(lines)

        # Pick the callback: prefer a freshly-built one from the
        # configured factory (Telegram with "🧠 Consolidated" prefix),
        # else reuse the original turn's callback as a best-effort.
        cb = None
        if self._consolidator_factory is not None:
            try:
                cb = self._consolidator_factory(
                    group.user_id, group.consolidator_cb,
                )
            except Exception:
                logger.debug(
                    "consolidator_factory raised for group %s",
                    group_id, exc_info=True,
                )
                cb = None
        if cb is None:
            cb = group.consolidator_cb

        if self._lane_queue is None:
            logger.warning(
                "Brain fan-out %s settled but no lane_queue wired — "
                "consolidation cannot fire; results will be lost",
                group_id,
            )
            return

        logger.info(
            "Brain fan-out %s consolidating %d results, enqueueing synthetic turn",
            group_id, len(group.results),
        )
        try:
            kwargs: dict = {}
            if cb is not None:
                kwargs["callback"] = cb
            if group.chat_session_id:
                kwargs["chat_session_id"] = group.chat_session_id
            result_text = await self._lane_queue.enqueue(
                group.user_id, synthetic_msg, **kwargs,
            )
        except Exception:
            logger.warning(
                "Brain fan-out %s consolidation enqueue failed",
                group_id, exc_info=True,
            )
            return

        # No-false-✅ guard — observation-only (the F1 machinery owns
        # rewrites elsewhere; don't duplicate a retry loop here). Fires
        # only when EVERY subagent failed yet the draft claims success.
        if (
            _failure_present
            and not _any_succeeded
            and result_text
            and draft_claims_success(result_text)
        ):
            logger.warning(
                "%s consolidation draft claims success while ALL %d "
                "subagents failed (group=%s) — draft preview: %.200s",
                COHERENCE_LOG_TAG, len(group.results), group_id,
                result_text,
            )

        # ── Streaming-ON terminal `done` for the live bubble (BUG 1) ─────
        # The synthetic turn above ran on the LANE QUEUE, NOT through the WS
        # request loop (``gateway.routes.chat_ws._run_one_turn``) that emits
        # the terminal ``{"type":"done","content":...}`` frame. With
        # ``bg_streaming`` ON (default) the reused live ``WebSocketCallback``
        # streamed the brain's tokens into a chat bubble — but no terminal
        # ever arrives on this path, so the bubble spins forever on
        # web/mobile. Emit the terminal here to settle it.
        #
        # Gated so there is NO double-send: quiet mode keeps its
        # ``background_done`` rescue below (streaming was OFF, no live
        # bubble); Telegram (non-web callback) has no ``send_terminal_done``
        # and is delivered during the turn by its own notifier. Duck-typed
        # (``hasattr``) so this runtime module needs no gateway import.
        if (
            result_text
            and not _quiet
            and is_live_web_callback(cb)
            and hasattr(cb, "send_terminal_done")
        ):
            try:
                await cb.send_terminal_done(result_text)
            except Exception:
                logger.debug(
                    "streaming-ON terminal done delivery failed for %s",
                    group_id, exc_info=True,
                )

        # ── Web quiet-mode delivery rescue (2026-06-04) ──────────────────
        # The synthetic turn above ran on the LANE QUEUE, NOT through the WS
        # request loop (``gateway.routes.chat_ws._run_agent_turn``). A live
        # ``WebSocketCallback`` with ``bg_streaming`` OFF buffers the brain's
        # tokens (see chat_ws ``on_event``: quiet mode drops live ``token``
        # frames) and relies on ``_run_agent_turn`` to flush the terminal
        # ``{"type":"done","content":...}`` frame carrying the full reply.
        # The lane-queue consolidation path never reaches that flush, so the
        # consolidated reply is produced then SILENTLY DROPPED — and because
        # the origin-aware router (consolidator_routing, 2026-06-03) picked
        # the live web callback, it never falls back to Telegram either.
        # Observed 2026-06-04: a Web-UI "check sheet" was answered with a
        # 986-char reply that reached neither the browser nor Telegram.
        #
        # Deliver it out-of-band via the ``background_done`` frame the Web UI
        # already renders (it is on quiet mode's always-allowed list). Gated
        # to web + quiet so Telegram (delivered during the turn by its
        # notifier) and web streaming-ON (live tokens already sent) never
        # double-send.
        if _quiet and result_text and is_live_web_callback(cb):
            try:
                await cb.on_event(AgentEvent(
                    "background_done",
                    "Consolidated reply",
                    {"name": "Consolidated", "result": result_text},
                ))
            except Exception:
                logger.debug(
                    "web consolidation rescue delivery failed for %s",
                    group_id, exc_info=True,
                )

    def list_running(self, user_id: str | None = None) -> list[dict]:
        """List running background tasks."""
        now = time.monotonic()
        result = []
        for tid, task in self._running.items():
            uid = self._task_users.get(tid, "")
            if user_id and uid != user_id:
                continue
            elapsed = now - self._task_starts.get(tid, now)
            result.append({
                "id": tid,
                "name": self._task_names.get(tid, tid[:8]),
                "user_id": uid,
                "status": "running",
                "elapsed": f"{elapsed:.0f}s",
                "elapsed_seconds": elapsed,
            })
        return result

    async def list_all(self, user_id: str, limit: int = 20) -> list[dict]:
        """List all tasks from DB (running + completed + failed).

        Includes the decrypted ``result`` body for completed tasks so the
        UI can render outcomes without a separate detail fetch. Also
        returns the Code Specialist visibility columns (mcp_prompt,
        mcp_transcript, workspace_dir, files_touched, short_description,
        goal_id) for the CodeSpecialist Web UI page; transcript and
        files_touched are decoded from JSON, prompt is decrypted.
        """
        import json as _json
        key = await get_user_dek(self._config, user_id)

        async with db_session(self._config) as db:
            rows = await db.execute(
                "SELECT id, name, status, error, result, created_at, "
                "completed_at, cost_usd, tokens_used, llm_calls, "
                "mcp_prompt, mcp_transcript, workspace_dir, "
                "files_touched, short_description, goal_id "
                "FROM background_tasks WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            results = await rows.fetchall()

        def _maybe_decrypt(value):
            if not value:
                return None
            try:
                return decrypt(value, key) if is_encrypted(value) else value
            except Exception:
                logger.debug("list_all: decrypt failed", exc_info=True)
                return None

        tasks = []
        for row in results:
            raw_result = row[4]
            decrypted_result = _maybe_decrypt(raw_result)

            # mcp_prompt is encrypted-at-rest. mcp_transcript +
            # files_touched are JSON strings (encrypted envelope so each
            # decrypts cleanly via _maybe_decrypt before JSON parse).
            mcp_prompt = _maybe_decrypt(row[10])
            transcript_raw = _maybe_decrypt(row[11])
            files_raw = _maybe_decrypt(row[13])
            mcp_transcript: list = []
            files_touched: list = []
            if transcript_raw:
                try:
                    mcp_transcript = _json.loads(transcript_raw) or []
                except Exception:
                    logger.debug("list_all: transcript JSON parse failed", exc_info=True)
            if files_raw:
                try:
                    files_touched = _json.loads(files_raw) or []
                except Exception:
                    logger.debug("list_all: files JSON parse failed", exc_info=True)

            tasks.append({
                "id": row[0],
                "name": row[1],
                "status": row[2],
                "error": row[3],
                "result": decrypted_result,
                "created_at": row[5],
                "completed_at": row[6],
                "cost_usd": row[7] or 0.0,
                "tokens_used": row[8] or 0,
                "llm_calls": row[9] or 0,
                "mcp_prompt": mcp_prompt,
                "mcp_transcript": mcp_transcript,
                "workspace_dir": row[12] or "",
                "files_touched": files_touched,
                "short_description": row[14] or "",
                "goal_id": row[15] or "",
            })
        return tasks

    async def cancel(self, task_id: str, user_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        uid = self._task_users.get(task_id)
        if uid != user_id:
            return False

        task = self._running.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled background task %s", task_id[:8])
            return True
        return False

    async def cancel_all(self) -> int:
        """Cancel all running tasks. Call on shutdown."""
        count = 0
        for tid, task in list(self._running.items()):
            if not task.done():
                task.cancel()
                count += 1

        # Wait for all to finish
        if self._running:
            await asyncio.gather(
                *self._running.values(), return_exceptions=True,
            )

        logger.info("Cancelled %d background tasks on shutdown", count)
        return count

    @property
    def running_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._running)
