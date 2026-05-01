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
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from lazyclaw.runtime.team_lead import TeamLead

from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import encrypt, decrypt, is_encrypted
from lazyclaw.db.connection import db_session
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime import task_event_bus

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Counter for human-readable task names
_task_counter = 0


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


# Maximum chars per task result included in the synthetic consolidation
# message. Aggressive truncation keeps the synthetic LLM call cheap.
_CONSOLIDATION_RESULT_PREVIEW = 1500


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
        # group_id → _BrainFanoutGroup. Cleaned up after _consolidate runs.
        self._brain_groups: dict[str, _BrainFanoutGroup] = {}

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

        async with db_session(self._config) as db:
            await db.execute(
                "INSERT INTO background_tasks "
                "(id, user_id, name, instruction, status, timeout) "
                "VALUES (?, ?, ?, ?, 'running', ?)",
                (task_id, user_id, task_name, encrypted_instruction, timeout),
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
        self._task_starts[task_id] = time.monotonic()
        self._task_provenance[task_id] = (source, fanout_group_id)

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
                elif event.kind == "tool_call" and _team_lead_ref is not None:
                    tool_name = (
                        (event.metadata or {}).get("display_name")
                        or (event.metadata or {}).get("tool")
                        or event.detail
                    )
                    try:
                        _team_lead_ref.update_step(_bound_task_id, str(tool_name))
                    except Exception:
                        logger.debug(
                            "team_lead.update_step failed for bg task %s",
                            _bound_task_id, exc_info=True,
                        )

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
            # Create FRESH Agent instance (isolated state, no race conditions)
            agent = Agent(
                config=self._config,
                router=self._router,
                registry=self._registry,
                eco_router=self._eco_router,
                permission_checker=self._permission_checker,
            )
            agent.is_background = True  # Browser uses headless in background

            async with asyncio.timeout(timeout):
                result = await agent.process_message(
                    user_id, instruction, callback=callback,
                )

            # Store result (encrypted) + cost stats from work_summary
            encrypted_result = encrypt(result, key)
            _cost = _captured_summary.total_cost if _captured_summary else 0.0
            _tokens = _captured_summary.total_tokens if _captured_summary else 0
            _calls = _captured_summary.llm_calls if _captured_summary else 0
            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE background_tasks SET status = 'done', result = ?, "
                    "cost_usd = ?, tokens_used = ?, llm_calls = ?, "
                    "completed_at = datetime('now') WHERE id = ?",
                    (encrypted_result, _cost, _tokens, _calls, task_id),
                )
                await db.commit()

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

            # Notify originator (e.g., team lead state cleanup)
            if on_complete:
                try:
                    await on_complete(task_id, _status)
                except Exception as exc:
                    logger.warning("on_complete callback failed for task %s: %s", task_id[:8], exc)

    # ── Brain fan-out consolidation ──────────────────────────────────

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

        # 1-result groups aren't fan-outs — fall back to the legacy per-
        # task push path so single run_background calls keep their
        # immediate "✅ done" UX.
        if len(group.results) == 1:
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
        lines = [
            f"[Background fan-out complete — {len(group.results)} tasks finished]",
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
                lines.append(preview or "(empty)")
            else:
                lines.append(f"FAILED: {r.error or 'unknown error'}")
            lines.append("")
        lines.extend([
            "Write ONE consolidated summary for the user. Don't repeat "
            "raw blobs — synthesize. Call out any failures explicitly. "
            "Keep it tight (~6-12 lines for Telegram).",
        ])
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
            await self._lane_queue.enqueue(
                group.user_id, synthetic_msg, **kwargs,
            )
        except Exception:
            logger.warning(
                "Brain fan-out %s consolidation enqueue failed",
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
        UI can render outcomes without a separate detail fetch.
        """
        key = await get_user_dek(self._config, user_id)

        async with db_session(self._config) as db:
            rows = await db.execute(
                "SELECT id, name, status, error, result, created_at, "
                "completed_at, cost_usd, tokens_used, llm_calls "
                "FROM background_tasks WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            results = await rows.fetchall()

        tasks = []
        for row in results:
            raw_result = row[4]
            decrypted_result: str | None = None
            if raw_result:
                try:
                    decrypted_result = (
                        decrypt(raw_result, key)
                        if is_encrypted(raw_result) else raw_result
                    )
                except Exception:
                    logger.debug("list_all: result decrypt failed", exc_info=True)
                    decrypted_result = None
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
