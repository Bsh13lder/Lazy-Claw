"""WebSocket chat endpoint for real-time streaming."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from lazyclaw.config import load_config
from lazyclaw.runtime.callbacks import AgentEvent, CancellationToken

logger = logging.getLogger(__name__)

ws_chat_router = APIRouter()

_config = load_config()

# Injected by app.py (same pattern as _lane_queue / _shared_registry).
# _task_runner + _team_lead are required so the Agent constructed for
# each WS session can dispatch background tasks and specialists — without
# them, the brain's run_background / dispatch_subagents tools both fail
# with "not configured" even though the runtime instances exist.
_lane_queue = None
_shared_registry = None
_task_runner = None
_team_lead = None


def set_chat_ws_deps(lane_queue, registry, task_runner=None, team_lead=None) -> None:
    """Called by app.py to inject shared dependencies."""
    global _lane_queue, _shared_registry, _task_runner, _team_lead
    _lane_queue = lane_queue
    _shared_registry = registry
    if task_runner is not None:
        _task_runner = task_runner
    if team_lead is not None:
        _team_lead = team_lead


# Keys excluded from forwarded metadata: bg_task_id / bg_task_name are
# already lifted to the top-level frame; raw bytes / unserializable values
# would crash send_json.
_BG_META_DROP = frozenset({"bg_task_id", "bg_task_name", "summary"})


def _safe_metadata(metadata) -> dict:
    """Return a JSON-safe copy of an event's metadata for client forwarding.

    Drops bg-task tags (already on the frame) and any non-primitive
    values that ``ws.send_json`` would refuse.
    """
    if not isinstance(metadata, dict):
        return {}
    out: dict = {}
    for k, v in metadata.items():
        if k in _BG_META_DROP:
            continue
        if isinstance(v, (str, int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = [
                x if isinstance(x, (str, int, float, bool, type(None))) else str(x)
                for x in v
            ]
        elif isinstance(v, dict):
            out[k] = {
                kk: vv if isinstance(vv, (str, int, float, bool, type(None))) else str(vv)
                for kk, vv in v.items()
            }
        # Skip everything else (bytes, dataclasses, etc.)
    return out


@dataclass
class WebSocketCallback:
    """Streams AgentEvents over a WebSocket connection."""

    ws: WebSocket
    cancel_token: CancellationToken = field(default_factory=CancellationToken)
    # Fix J — mutable holder for the user's bg-streaming preference. The
    # outer WS handler scope owns it (refreshed from DB at each turn
    # start) and both cb.on_event and _task_event_pump read it via this
    # reference. When ``streaming_state["bg_streaming"]`` is False, live
    # bg_* frames are dropped (final ``background_done`` /
    # ``background_failed`` always pass through so the user still sees
    # the result).
    streaming_state: dict = field(default_factory=lambda: {"bg_streaming": True})
    _buffer: str = field(default="", init=False)
    _closed: bool = field(default=False, init=False)
    _work_summary: dict | None = field(default=None, init=False)
    # Set by the agent's "done" event so the WS "done" payload can expose
    # model_used + fallback_reason to the client (fallback chip in Web UI).
    _final_model: str | None = field(default=None, init=False)
    _fallback_reason: str | None = field(default=None, init=False)
    # Side-channel notes the user typed while the agent was already running.
    # Drained by the agent loop between TAOR iterations and injected as
    # system messages so the agent can acknowledge or pivot mid-run.
    _side_notes: list = field(default_factory=list, init=False)
    # request_id → asyncio.Future[bool] for pending approval prompts.
    # The reader loop resolves these when the client posts an
    # `approval_response` frame.
    _pending_approvals: dict = field(default_factory=dict, init=False)

    def push_side_note(self, text: str) -> None:
        """Queue a side-channel note from the user for the running agent."""
        if text and text.strip():
            self._side_notes.append(text.strip())

    def pop_side_notes(self) -> list:
        """Return and clear pending side notes (agent-side polled)."""
        if not self._side_notes:
            return []
        notes = list(self._side_notes)
        self._side_notes.clear()
        return notes

    async def _send(self, data: dict) -> None:
        if self._closed:
            return
        try:
            if self.ws.client_state == WebSocketState.CONNECTED:
                await self.ws.send_json(data)
        except (WebSocketDisconnect, RuntimeError):
            self._closed = True
            self.cancel_token.cancel()

    async def on_event(self, event: AgentEvent) -> None:
        kind = event.kind
        # Demux: events tagged with bg_task_id come from a background task
        # (see TaskRunner._BgEventTap). Re-emit as bg_* frames so the chat
        # UI renders them under a "Background: <name>" card instead of
        # appending to the foreground turn's tool list (which has likely
        # already been finalized by the brain's "done" event).
        _bg_id = (event.metadata or {}).get("bg_task_id") if isinstance(event.metadata, dict) else None
        _bg_name = (event.metadata or {}).get("bg_task_name") if isinstance(event.metadata, dict) else None

        # ── Fix J + L + M — streaming OFF filter ───────────────────
        # When the user has `bg_streaming` toggled OFF (via `/streaming
        # off` chat command or the Web UI toggle), drop every non-text
        # frame here. Quiet mode = team lead's voice only — the brain's
        # tokens + the final consolidated reply, nothing else.
        #
        # Frames killed in quiet mode:
        #   - bg_* anything tagged with bg_task_id (live run_background
        #     progress, tokens, tool calls)
        #   - specialist_* (delegate-spawned specialist progress + done)
        #   - team_delegate (the "delegating to X" announcement)
        #   - tool_call / tool_result for the brain's OWN foreground
        #     tools (search_tools, MCP calls, recall_memories, etc. —
        #     these are the "ACTING 36s step 1" tool cards that made
        #     the chat look stuck in the 2026-05-14 screenshot)
        #   - phase (ACTING/THINKING header chips)
        #
        # ALWAYS allowed through, even in quiet mode:
        #   - token (the brain's text — this IS the team lead speaking)
        #   - thinking_delta / thinking_done (collapsible panel, opt-in)
        #   - background_done / background_failed (terminal events —
        #     either painted directly when streaming ON, or absorbed via
        #     side-note + brain consolidation when streaming OFF, per
        #     task_runner._consolidate's quiet-mode branch)
        #   - plan_pending / plan_question / plan_approved (gates the
        #     user must answer — never silently dropped)
        #   - approval_request (gates a skill run — never silently
        #     dropped or auto-denied)
        #   - cancelled / done / work_summary (control frames)
        _streaming_off = not self.streaming_state.get("bg_streaming", True)
        if _streaming_off:
            _is_worker_noise = (
                (_bg_id and kind not in (
                    "background_done", "background_failed", "work_summary",
                ))
                or kind in (
                    "specialist_start",
                    "specialist_thinking",
                    "specialist_tool",
                    "specialist_done",
                    "team_delegate",
                    "tool_call",
                    "tool_result",
                    "phase",
                )
            )
            if _is_worker_noise:
                return

        # ── Bg leak guard ────────────────────────────────────────────
        # If this event is tagged as background, ALL non-tool-call kinds
        # must be re-emitted as a single `bg_event` frame so they don't
        # leak into the foreground chat as if the brain were still
        # talking. tool_call / tool_result keep their existing dedicated
        # bg_* frames; background_done / background_failed are direct
        # surface (the user must see those even when bg-tagged).
        if _bg_id and kind not in (
            "tool_call",
            "tool_result",
            "background_done",
            "background_failed",
            "work_summary",  # captured-only, never forwarded
        ):
            await self._send({
                "type": "bg_event",
                "task_id": _bg_id,
                "task_name": _bg_name,
                "kind": kind,
                "detail": event.detail,
                "metadata": _safe_metadata(event.metadata),
            })
            return

        if kind == "token":
            # Always accumulate — the terminal "done" frame uses
            # ``cb._buffer`` as fallback content so the user still sees
            # the full reply even when live streaming is suppressed.
            self._buffer += event.detail
            # Quiet mode (bg_streaming=False) — DO NOT forward the live
            # token frame to the WS. Client gets the complete reply in
            # one shot via the terminal "done" payload below. Matches
            # the 8e378e2 commit-message intent ("token frames dropped
            # when streaming is off") that the original code only
            # honored for bg-tagged tokens, leaking foreground brain
            # tokens through.
            if not self.streaming_state.get("bg_streaming", True):
                return
            await self._send({"type": "token", "content": event.detail})

        elif kind == "tool_call":
            name = event.metadata.get("tool_name", event.detail)
            args = event.metadata.get("arguments", {})
            tool_call_id = event.metadata.get("tool_call_id")
            if _bg_id:
                await self._send({
                    "type": "bg_tool_call",
                    "task_id": _bg_id,
                    "task_name": _bg_name,
                    "name": name,
                    "args": args,
                    "tool_call_id": tool_call_id,
                })
            else:
                await self._send({
                    "type": "tool_call",
                    "name": name,
                    "args": args,
                    "tool_call_id": tool_call_id,
                })

        elif kind == "tool_result":
            name = event.metadata.get("tool_name", event.detail)
            result = event.metadata.get("result", "")
            preview = result[:200] if isinstance(result, str) else str(result)[:200]
            tool_call_id = event.metadata.get("tool_call_id")
            if _bg_id:
                await self._send({
                    "type": "bg_tool_result",
                    "task_id": _bg_id,
                    "task_name": _bg_name,
                    "name": name,
                    "preview": preview,
                    "tool_call_id": tool_call_id,
                })
            else:
                await self._send({
                    "type": "tool_result",
                    "name": name,
                    "preview": preview,
                    "tool_call_id": tool_call_id,
                })

        elif kind == "team_delegate":
            await self._send({
                "type": "team_delegate",
                "name": event.detail,
                "specialist": event.metadata.get("specialist", ""),
                "instruction": event.metadata.get("instruction", ""),
            })

        elif kind == "specialist_start":
            await self._send({
                "type": "specialist_start",
                "name": event.metadata.get("specialist", event.detail),
                "task": event.metadata.get("task", ""),
            })

        elif kind == "specialist_thinking":
            await self._send({
                "type": "specialist_thinking",
                "specialist": event.metadata.get("specialist", event.detail),
                "iteration": event.metadata.get("iteration"),
            })

        elif kind == "specialist_tool":
            await self._send({
                "type": "specialist_tool",
                "specialist": event.metadata.get("specialist", ""),
                "tool": event.metadata.get("tool", event.detail),
            })

        elif kind == "specialist_done":
            await self._send({
                "type": "specialist_done",
                "name": event.metadata.get("specialist", event.detail),
                "success": event.metadata.get("success"),
                "duration_ms": event.metadata.get("duration_ms"),
                "tools_used": event.metadata.get("tools_used"),
                "error": event.metadata.get("error"),
            })

        elif kind == "phase":
            # TAOR phase transition — think|act|observe|reflect.
            await self._send({
                "type": "phase",
                "phase": event.metadata.get("phase", event.detail),
                "iteration": event.metadata.get("iteration"),
                "tools": event.metadata.get("tools"),
            })

        elif kind == "side_note_ack":
            await self._send({
                "type": "side_note_ack",
                "message": event.detail,
            })

        elif kind == "plan_pending":
            # The agent produced a plan and is blocked waiting for approval.
            await self._send({
                "type": "plan_pending",
                "plan": event.metadata.get("plan", event.detail),
                "steps": event.metadata.get("steps", []),
            })

        elif kind == "plan_question":
            # Agent needs one piece of info before it can plan.
            await self._send({
                "type": "plan_question",
                "question": event.metadata.get("question", event.detail),
            })

        elif kind == "plan_approved":
            await self._send({
                "type": "plan_approved",
                "auto_approve_session": event.metadata.get(
                    "auto_approve_session", False,
                ),
            })

        elif kind == "thinking_delta":
            # Model reasoning token — surfaced for the collapsible
            # Thinking panel in the Web UI.
            await self._send({
                "type": "thinking_delta",
                "content": event.detail,
            })

        elif kind == "thinking_done":
            await self._send({"type": "thinking_done"})

        elif kind == "work_summary":
            from dataclasses import asdict
            raw = event.metadata.get("summary")
            self._work_summary = asdict(raw) if hasattr(raw, "__dataclass_fields__") else raw

        elif kind == "cancelled":
            await self._send({"type": "cancelled"})

        elif kind == "done":
            # Capture model + fallback so _run_agent_turn can include them
            # on the "done" payload the client listens for.
            self._final_model = event.metadata.get("model_used") or self._final_model
            self._fallback_reason = event.metadata.get("fallback_reason")

        elif kind in ("background_done", "background_failed"):
            # Direct surface for 1-task brain fan-outs: TaskRunner._consolidate
            # fires this on the original chat callback (not via the event bus,
            # which is filtered for brain-source frames to avoid duplicates
            # with multi-task consolidation turns). Without this branch, the
            # event silently fell through and the chat UI sat in "running…"
            # forever even after the task completed. Fixed 2026-04-29 after
            # the Valencia salons bg task wrote to the sheet but the user was
            # never notified. See plans/silent-completion-fix.
            await self._send({
                "type": kind,
                "name": event.metadata.get("name", event.detail),
                "task_id": event.metadata.get("task_id"),
                "result": event.metadata.get("result"),
                "error": event.metadata.get("error"),
                "duration_ms": event.metadata.get("duration_ms"),
                "tools_used": event.metadata.get("tools_used"),
                "total_cost": event.metadata.get("total_cost"),
            })

    async def on_approval_request(self, skill_name: str, arguments: dict) -> bool:
        """Round-trip an approval prompt to the connected web user.

        Sends ``{type: "approval_request", request_id, skill, args}``
        over the WS and awaits a matching ``approval_response`` frame
        (resolved by the reader loop). Defaults to **deny** on
        timeout, disconnect, or any error — never auto-approves
        silently.

        Set ``LAZYCLAW_AUTO_APPROVE=1`` to restore the legacy
        "auto-approve everything" behaviour for trusted self-host
        setups (CI, single-user dev). Default is OFF — every gated
        skill triggers a real prompt.
        """
        if os.environ.get("LAZYCLAW_AUTO_APPROVE", "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            return True
        if self._closed or self.ws.client_state != WebSocketState.CONNECTED:
            return False
        request_id = uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_approvals[request_id] = future
        try:
            await self._send({
                "type": "approval_request",
                "request_id": request_id,
                "skill": skill_name,
                "args": _safe_metadata(arguments),
            })
            try:
                # 2-minute hard cap so a forgotten dialog can't pin a
                # tool execution forever. Long enough for the user to
                # decide, short enough to fail loudly when ignored.
                approved = await asyncio.wait_for(future, timeout=120)
                return bool(approved)
            except asyncio.TimeoutError:
                logger.info(
                    "approval %s for %s timed out — denying",
                    request_id, skill_name,
                )
                return False
        except Exception:
            logger.debug(
                "approval %s for %s failed — denying",
                request_id, skill_name, exc_info=True,
            )
            return False
        finally:
            self._pending_approvals.pop(request_id, None)

    def resolve_approval(self, request_id: str, approved: bool) -> bool:
        """Settle a pending approval future (called by the reader loop).

        Returns True when the request_id matched a live future.
        """
        future = self._pending_approvals.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    async def on_help_request(self, context: str, needs_browser: bool) -> str:
        return "skip"


def _allowed_origins() -> set[str]:
    """Return the configured allowed WS origins.

    Sourced from the same gateway ``cors_origin`` config the CORS
    middleware uses (``lazyclaw/gateway/app.py`` passes
    ``[_config.cors_origin]`` to ``CORSMiddleware``). Supports a
    comma-separated list so multi-origin deployments work, and trims a
    trailing slash so ``http://host/`` matches the browser-sent
    ``http://host`` Origin.
    """
    raw = getattr(_config, "cors_origin", "") or ""
    return {
        o.strip().rstrip("/")
        for o in raw.split(",")
        if o.strip()
    }


def _origin_allowed(ws: WebSocket) -> bool:
    """Validate the WS handshake ``Origin`` header (CSWSH defense).

    The session cookie is ``samesite=lax``, which does NOT block
    cross-site WebSocket handshakes, so a malicious page could open a
    WS with the victim's cookie. We mitigate by checking ``Origin``:

    - Origin ABSENT  → ALLOW. Native / non-browser clients (CLI, mobile,
      tests) legitimately omit it; rejecting would break them. Browsers
      always send Origin on cross-origin WS, so an absent Origin is not
      an attacker vector here.
    - Origin PRESENT and matches a configured origin → ALLOW.
    - Origin PRESENT and does NOT match → REJECT.
    """
    origin = ws.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") in _allowed_origins()


async def _authenticate_ws(ws: WebSocket):
    """Authenticate WebSocket via session cookie + Origin check.

    Returns ``None`` when the handshake should be refused — either the
    Origin failed the CSWSH check or the session cookie was missing /
    invalid.
    """
    from lazyclaw.gateway.auth import get_session_user

    if not _origin_allowed(ws):
        logger.warning(
            "WebSocket chat rejected: disallowed Origin %r",
            ws.headers.get("origin"),
        )
        return None

    session_id = ws.cookies.get("session_id")
    if not session_id:
        return None
    return await get_session_user(_config, session_id)


async def _run_agent_turn(
    user_id: str,
    content: str,
    session_id: str | None,
    cb: WebSocketCallback,
) -> str:
    """Single agent turn — enqueue via lane or run directly."""
    if _lane_queue:
        return await _lane_queue.enqueue(
            user_id, content,
            callback=cb,
            chat_session_id=session_id,
        )

    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.permissions.checker import PermissionChecker
    from lazyclaw.runtime.agent import Agent
    from lazyclaw.skills.registry import SkillRegistry

    registry = _shared_registry or SkillRegistry()
    if not _shared_registry:
        registry.register_defaults(config=_config)
    router = LLMRouter(_config)
    checker = PermissionChecker(_config, registry)
    # Pull task_runner + team_lead from the shared deps so this WS
    # Agent can dispatch background tasks and specialists. Without these
    # the brain's run_background / dispatch_subagents tools fail with
    # "not configured" — observed on 2026-05-14 in production when a
    # Google Workspace task forced the brain into a 10-iteration loop.
    agent = Agent(
        _config, router, registry,
        permission_checker=checker,
        task_runner=_task_runner,
        team_lead=_team_lead,
    )
    agent.cancel_token = cb.cancel_token
    return await agent.process_message(
        user_id, content, callback=cb, chat_session_id=session_id,
    )


@ws_chat_router.websocket("/ws/chat")
async def chat_websocket(ws: WebSocket):
    # CSWSH defense — reject a cross-site handshake before touching the
    # session cookie. 1008 = policy violation.
    if not _origin_allowed(ws):
        logger.warning(
            "WebSocket chat rejected: disallowed Origin %r",
            ws.headers.get("origin"),
        )
        await ws.close(code=1008, reason="Origin not allowed")
        return

    user = await _authenticate_ws(ws)
    if not user:
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    logger.info("WebSocket chat connected: user=%s", user.username)

    # Mutable holder — shared between reader task and writer tasks.
    state: dict = {"active": None}  # type: dict[str, WebSocketCallback | None]
    writer_tasks: set = set()

    # Fix J — bg-streaming preference shared with every per-turn cb and
    # with _task_event_pump. Init from DB on connect; refreshed at each
    # turn start (so a `/streaming on|off` chat command takes effect on
    # the very next turn / on bg events arriving after that turn).
    streaming_state: dict = {"bg_streaming": True}
    try:
        from lazyclaw.runtime.streaming_setting import get_bg_streaming
        streaming_state["bg_streaming"] = await get_bg_streaming(_config, user.id)
    except Exception:
        logger.debug("initial bg_streaming load failed", exc_info=True)
    # Subagent terminal events that fired while the brain was idle. The
    # task_event_bus pump can't push them into a callback that doesn't
    # exist, so we stash them here and drain into the next turn's
    # callback as system-level side-notes. Without this the brain never
    # learns the outcome of background subagents and can't write the
    # consolidated summary the user expects.
    pending_subagent_notes: list[str] = []

    # Forward live browser events from the per-user pub/sub bus.
    # Runs alongside the chat-message reader; cancelled on disconnect.
    async def _browser_event_pump() -> None:
        from lazyclaw.browser.event_bus import recent_events, subscribe

        # Initial paint: send last 4 events so the canvas mounts with state.
        # Only replay events from the last 5 min — matches the frontend's
        # auto-clear window so a long-idle ring buffer doesn't mount a
        # stale BrowserCanvas on reconnect.
        try:
            for evt in recent_events(user.id, limit=4, max_age_s=300):
                payload = {"type": "browser_event", **evt.to_frame()}
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(payload)
        except Exception:
            logger.debug("Initial browser-event paint failed", exc_info=True)
        try:
            async for evt in subscribe(user.id):
                if ws.client_state != WebSocketState.CONNECTED:
                    return
                try:
                    await ws.send_json({"type": "browser_event", **evt.to_frame()})
                except Exception:
                    logger.debug("browser_event send failed", exc_info=True)
                    return
        except asyncio.CancelledError:
            pass

    bus_task = asyncio.create_task(_browser_event_pump())

    # Forward background-task lifecycle events so Activity / Overview can
    # update without 3s poll lag, and so background_done from earlier
    # turns still surface in chat. Subagent terminal events get special
    # routing: they NEVER render in chat (the brain writes ONE
    # consolidated summary instead), they ONLY feed the brain's
    # side-note channel — either immediately if a turn is active, or
    # queued in `pending_subagent_notes` until the next turn starts.
    async def _task_event_pump() -> None:
        from lazyclaw.runtime import task_event_bus

        # Initial paint: replay recent live-progress events ONLY (so the
        # Activity panel knows what's still running). Terminal completion
        # events are NOT replayed — the brain has already incorporated
        # them (or will, via pending_subagent_notes), so re-painting them
        # would duplicate "✅ Background task completed" cards in chat.
        try:
            for evt in task_event_bus.recent_events(
                user.id, limit=10, max_age_s=300,
            ):
                if evt.kind in ("background_done", "background_failed"):
                    continue
                payload = {"type": evt.kind, **evt.to_frame()}
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(payload)
        except Exception:
            logger.debug("Initial task-event paint failed", exc_info=True)
        try:
            async for evt in task_event_bus.subscribe(user.id):
                if ws.client_state != WebSocketState.CONNECTED:
                    return

                _is_subagent_terminal = (
                    evt.kind in ("background_done", "background_failed")
                    and (evt.task_id or "").startswith(("subagent-", "specialist-"))
                )
                # Brain-spawned run_background tasks share the same
                # "consolidate, don't paint per-task" rule. TaskRunner
                # already fired the synthetic consolidation turn (or
                # will, when the last sibling settles), and that turn's
                # reply lands in chat normally. Per-task cards would
                # duplicate.
                #
                # User-source tasks (compound-split sub-tasks, manual
                # run_background calls) also paint via the direct
                # `on_event` callback at chat_ws.py:373 — forwarding the
                # bus copy too produced visible duplicate completion
                # cards (2026-05-16 Gerardo case: send_whatsapp +
                # save_contact each rendered twice). Cron-source events
                # have NO WS callback, so the bus is their only path —
                # those keep flowing through.
                _is_brain_bg_terminal = (
                    evt.kind in ("background_done", "background_failed")
                    and getattr(evt, "source", None) in ("brain", "user")
                )

                # Build the side-note payload the brain will see on its
                # next TAOR iteration. Subagent results never render as
                # chat messages — the brain consolidates them.
                if _is_subagent_terminal:
                    # RC2 — when the subagent belongs to a consolidating
                    # fan-out (dispatch_subagents wired through TaskRunner),
                    # the consolidation turn (task_runner._consolidate)
                    # delivers ONE merged reply. Drop the per-subagent
                    # side-note here so the result isn't ALSO injected as a
                    # pending note → delivered twice. (2026-05-29 fix.)
                    if getattr(evt, "fanout_group_id", None):
                        continue
                    if evt.kind == "background_done":
                        note = (
                            f"[bg {evt.task_id} done] "
                            f"{evt.name}: {(evt.result or '')[:600]}"
                        )
                    else:
                        note = (
                            f"[bg {evt.task_id} failed] "
                            f"{evt.name}: {evt.error or 'unknown error'}"
                        )
                    active_cb = state.get("active")
                    if active_cb is not None:
                        try:
                            active_cb.push_side_note(note)
                        except Exception:
                            logger.debug(
                                "push_side_note for subagent failed",
                                exc_info=True,
                            )
                    else:
                        # Brain idle — queue until next turn starts.
                        pending_subagent_notes.append(note)
                    # DO NOT forward subagent terminal frames to chat —
                    # they'd render as standalone "✅ Background task
                    # completed" assistant cards, breaking the "ONE
                    # consolidated summary" contract. Activity panel
                    # already saw the task_completed frame separately.
                    continue

                if _is_brain_bg_terminal:
                    # Per-task assistant cards for brain fan-out
                    # tasks would duplicate the consolidator's reply.
                    # Drop them; Activity panel still updates via the
                    # separate task_completed frame.
                    continue

                # Fix J — streaming-OFF filter for the event-bus path.
                # Terminal events (background_done / background_failed)
                # still surface so the user sees the result; live
                # progress events are dropped when the user has
                # `bg_streaming` toggled off.
                if (
                    not streaming_state.get("bg_streaming", True)
                    and evt.kind not in (
                        "background_done",
                        "background_failed",
                    )
                ):
                    continue

                try:
                    await ws.send_json({"type": evt.kind, **evt.to_frame()})
                except Exception:
                    logger.debug("task_event send failed", exc_info=True)
                    return
        except asyncio.CancelledError:
            pass

    task_bus_task = asyncio.create_task(_task_event_pump())

    async def _maybe_append_correction(
        user_id: str,
        source: str,
        text: str,
        cb: "WebSocketCallback",
    ) -> None:
        """If a templated run is active for this user, append a correction
        to the template's playbook and emit a UI toast frame.

        Cheap to call on every side-note / reject — bails fast when no
        template is active.
        """
        from lazyclaw.browser import active_templates
        from lazyclaw.browser import template_corrections
        from lazyclaw.browser import templates as tpl_store

        tpl_id = active_templates.get_active(user_id)
        if not tpl_id:
            return
        try:
            updated = await template_corrections.append_correction(
                _config, user_id, tpl_id, source, text,
            )
        except Exception:
            logger.warning("append_correction failed", exc_info=True)
            return
        if updated is None:
            return
        try:
            tpl = await tpl_store.get_template(_config, user_id, tpl_id)
        except Exception:
            tpl = updated
        try:
            await cb._send({
                "type": "template_correction_added",
                "template_id": tpl_id,
                "name": (tpl or updated).get("name") if (tpl or updated) else "",
                "source": source,
                "corrections_pending": (tpl or updated).get("corrections_pending", 1)
                if (tpl or updated) else 1,
            })
        except Exception:
            logger.debug("template_correction_added emit failed", exc_info=True)

    async def _maybe_autosave_template(
        user_id: str, turn_start_ts: float, cb: "WebSocketCallback"
    ) -> None:
        """Post-turn hook — auto-save a template when the user just completed
        a successful multi-step browser flow.

        Strict success gate (all must hold):
          • turn returned without exception (caller-enforced)
          • ≥1 navigate event with action=goto
          • ≥3 action events (click/type/goto/scroll/press_key)
          • ≥1 checkpoint event resolved=approved
          • 0 checkpoint events resolved=rejected (correction path, phase 2)

        On match, upserts by primary host — re-running the same site updates
        ONE row that gets sharper over time, never duplicates. Emits a
        ``template_saved`` WS frame for a UI toast (UI-only, never re-enters
        LLM context).
        """
        from lazyclaw.browser import event_bus
        from lazyclaw.browser import templates as tpl_store
        from lazyclaw.browser.template_synth import (
            synthesize_template_from_events,
        )
        from lazyclaw.llm.router import LLMRouter
        from lazyclaw.settings.general import get_general_settings

        # Respect user opt-out before any work.
        try:
            general = await get_general_settings(_config, user_id)
        except Exception:
            general = {}
        if not general.get("auto_save_browser_templates", True):
            return

        events = event_bus.recent_events(
            user_id, limit=50, max_age_s=max(1.0, __import__("time").time() - turn_start_ts),
        )
        if not events:
            return

        action_events = [
            e for e in events
            if e.kind in ("action", "navigate")
            and getattr(e, "action", None) in ("click", "type", "goto", "scroll", "press_key")
        ]
        nav_events = [
            e for e in action_events if getattr(e, "action", None) == "goto"
        ]

        approved = 0
        rejected = 0
        for e in events:
            if e.kind != "checkpoint":
                continue
            extra = getattr(e, "extra", None) or {}
            resolved = extra.get("resolved") if isinstance(extra, dict) else None
            if resolved == "approved":
                approved += 1
            elif resolved == "rejected":
                rejected += 1

        if rejected > 0:
            # User rejected at least one checkpoint — phase 2 will route this
            # to correction-capture instead. For now, silently skip.
            return
        if not nav_events or len(action_events) < 3 or approved < 1:
            return

        # Synthesize the draft (worker LLM polishes the playbook). Re-uses
        # the same path as the BrowserCanvas "Save as template" button.
        try:
            draft = await synthesize_template_from_events(
                _config, LLMRouter(_config), user_id, since_ts=turn_start_ts,
            )
        except Exception:
            logger.warning("autosave: synthesize failed", exc_info=True)
            return
        if draft is None or not draft.setup_urls:
            return

        host = tpl_store._host_of(draft.setup_urls[0])
        if not host:
            return

        try:
            tpl, was_created = await tpl_store.upsert_by_host(
                _config, user_id,
                host=host,
                name=draft.name,
                icon=draft.icon,
                setup_urls=draft.setup_urls,
                checkpoints=draft.checkpoints,
                playbook=draft.playbook,
                auto_saved=True,
            )
        except Exception:
            logger.warning("autosave: upsert_by_host failed", exc_info=True)
            return

        # Stamp as active so any user follow-up in the next short window
        # (e.g. "actually, you should have done X") routes through
        # template_corrections.append_correction.
        from lazyclaw.browser import active_templates
        active_templates.set_active(user_id, tpl["id"])

        await cb._send({
            "type": "template_saved",
            "template_id": tpl["id"],
            "name": tpl["name"],
            "icon": tpl.get("icon"),
            "host": host,
            "was_created": was_created,
            "run_count": tpl.get("run_count", 1),
        })

    async def _run_one_turn(content: str, session_id: str | None) -> None:
        """Run a single agent turn as its own task. Shared state['active']
        points at the current callback so the reader can route side-notes
        and cancels to it."""
        import time as _time

        # Fix J — refresh streaming preference from DB at turn start so a
        # `/streaming on|off` from the prior turn takes effect immediately.
        try:
            from lazyclaw.runtime.streaming_setting import get_bg_streaming
            streaming_state["bg_streaming"] = await get_bg_streaming(
                _config, user.id,
            )
        except Exception:
            logger.debug("turn-start bg_streaming refresh failed", exc_info=True)
        cb = WebSocketCallback(ws=ws, streaming_state=streaming_state)
        # Drain background-arrived subagent notes into this turn's
        # callback BEFORE marking it active. The agent's TAOR loop
        # pop_side_notes() on each iteration → these land as system
        # messages and the brain writes ONE consolidated summary
        # incorporating the prior fan-out's results.
        if pending_subagent_notes:
            for _note in pending_subagent_notes:
                try:
                    cb.push_side_note(_note)
                except Exception:
                    logger.debug("draining pending subagent note failed",
                                 exc_info=True)
            logger.info(
                "Drained %d pending subagent note(s) into new turn for user %s",
                len(pending_subagent_notes), user.id,
            )
            pending_subagent_notes.clear()
        state["active"] = cb
        turn_start_ts = _time.time()
        try:
            result = await _run_agent_turn(user.id, content, session_id, cb)
            # Auto-save successful multi-step browser flows as templates.
            # UI-only frame — never enters LLM context. Strict success gate
            # (≥1 nav + ≥3 actions + ≥1 approved checkpoint + 0 rejects).
            try:
                await _maybe_autosave_template(user.id, turn_start_ts, cb)
            except Exception:
                logger.debug("template autosave emit failed", exc_info=True)
            done_payload: dict = {
                "type": "done",
                "content": result or cb._buffer,
            }
            if cb._work_summary:
                done_payload["usage"] = cb._work_summary
            if cb._final_model:
                done_payload["model_used"] = cb._final_model
            if cb._fallback_reason:
                done_payload["fallback_reason"] = cb._fallback_reason
            await cb._send(done_payload)
        except asyncio.CancelledError:
            await cb._send({"type": "cancelled"})
        except Exception as exc:
            logger.error("WebSocket chat turn error: %s", exc, exc_info=True)
            await cb._send({"type": "error", "message": str(exc)})
        finally:
            # Only clear state.active if it's still this callback
            if state.get("active") is cb:
                state["active"] = None
            # Clear the per-user 'active template' marker so corrections
            # in the next turn don't get mis-routed to this template's
            # playbook. autosave/run_browser_template re-set it next time.
            try:
                from lazyclaw.browser import active_templates
                active_templates.clear_active(user.id)
            except Exception:
                logger.debug("clear_active failed", exc_info=True)

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if msg_type == "cancel":
                cb = state.get("active")
                if cb is not None:
                    cb.cancel_token.cancel()
                continue

            if msg_type == "approval_response":
                request_id = (data.get("request_id") or "").strip()
                approved = bool(data.get("approved"))
                cb = state.get("active")
                if cb is not None and request_id:
                    settled = cb.resolve_approval(request_id, approved)
                    if not settled:
                        logger.debug(
                            "approval_response %s did not match a "
                            "pending request (timeout / dup?)",
                            request_id,
                        )
                continue

            if msg_type == "side_note":
                # Explicit side-channel message — append to running agent.
                note = (data.get("content") or "").strip()
                cb = state.get("active")
                if cb is not None and note:
                    cb.push_side_note(note)
                    await ws.send_json({
                        "type": "side_note_ack",
                        "message": note[:80],
                    })
                    # If a templated run is active, this side-note IS the
                    # user tutoring the agent — append to the playbook.
                    asyncio.create_task(
                        _maybe_append_correction(user.id, "side_note", note, cb),
                    )
                elif cb is None and note:
                    # No agent running — just treat it as a normal message.
                    msg_type = "message"
                    data["content"] = note
                else:
                    continue

            if msg_type == "message":
                content = (data.get("content") or "").strip()
                if not content:
                    await ws.send_json({"type": "error", "message": "Empty message"})
                    continue

                session_id = data.get("session_id")

                # If an agent turn is already running, auto-promote this
                # message to a side-note so the user doesn't have to
                # remember which button to press. Also echo it back as a
                # visible chat bubble with a "queued" badge so the message
                # doesn't silently vanish into a ThinkingCard chip.
                cb = state.get("active")
                if cb is not None:
                    cb.push_side_note(content)
                    await ws.send_json({
                        "type": "queued_user_message",
                        "content": content,
                    })
                    await ws.send_json({
                        "type": "side_note_ack",
                        "message": content[:80],
                    })
                    continue

                # Otherwise start a new turn as its own background task —
                # keeps the reader loop free to accept side-notes + cancels.
                task = asyncio.create_task(_run_one_turn(content, session_id))
                writer_tasks.add(task)
                task.add_done_callback(writer_tasks.discard)

    except WebSocketDisconnect:
        logger.info("WebSocket chat disconnected: user=%s", user.username)
    except Exception as exc:
        logger.error("WebSocket unexpected error: %s", exc, exc_info=True)
    finally:
        # All exit paths converge here so the per-connection pumps and
        # spawned writer tasks always get cancelled — including the
        # FastAPI normal-shutdown path that doesn't surface as
        # WebSocketDisconnect. Without this, the browser_event /
        # task_event subscriptions accumulated as zombies per
        # disconnect and leaked memory in long-running servers.
        cb = state.get("active")
        if cb is not None:
            try:
                cb.cancel_token.cancel()
            except Exception:
                logger.debug("cancel_token.cancel() raised", exc_info=True)
        for t in writer_tasks:
            t.cancel()
        bus_task.cancel()
        task_bus_task.cancel()
