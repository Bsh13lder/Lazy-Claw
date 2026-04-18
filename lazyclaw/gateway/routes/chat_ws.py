"""WebSocket chat endpoint for real-time streaming."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from lazyclaw.config import load_config
from lazyclaw.runtime.callbacks import AgentEvent, CancellationToken

logger = logging.getLogger(__name__)

ws_chat_router = APIRouter()

_config = load_config()

# Injected by app.py (same pattern as _lane_queue / _shared_registry)
_lane_queue = None
_shared_registry = None


def set_chat_ws_deps(lane_queue, registry) -> None:
    """Called by app.py to inject shared dependencies."""
    global _lane_queue, _shared_registry
    _lane_queue = lane_queue
    _shared_registry = registry


@dataclass
class WebSocketCallback:
    """Streams AgentEvents over a WebSocket connection."""

    ws: WebSocket
    cancel_token: CancellationToken = field(default_factory=CancellationToken)
    _buffer: str = field(default="", init=False)
    _closed: bool = field(default=False, init=False)
    _work_summary: dict | None = field(default=None, init=False)
    # Side-channel notes the user typed while the agent was already running.
    # Drained by the agent loop between TAOR iterations and injected as
    # system messages so the agent can acknowledge or pivot mid-run.
    _side_notes: list = field(default_factory=list, init=False)

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

        if kind == "token":
            self._buffer += event.detail
            await self._send({"type": "token", "content": event.detail})

        elif kind == "tool_call":
            name = event.metadata.get("tool_name", event.detail)
            args = event.metadata.get("arguments", {})
            await self._send({"type": "tool_call", "name": name, "args": args})

        elif kind == "tool_result":
            name = event.metadata.get("tool_name", event.detail)
            result = event.metadata.get("result", "")
            preview = result[:200] if isinstance(result, str) else str(result)[:200]
            await self._send({"type": "tool_result", "name": name, "preview": preview})

        elif kind == "specialist_start":
            await self._send({
                "type": "specialist_start",
                "name": event.metadata.get("specialist", event.detail),
                "task": event.metadata.get("task", ""),
            })

        elif kind == "specialist_done":
            await self._send({
                "type": "specialist_done",
                "name": event.metadata.get("specialist", event.detail),
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
            # Final done event sent by the endpoint after processing completes
            pass

    async def on_approval_request(self, skill_name: str, arguments: dict) -> bool:
        # Auto-approve from web UI for now
        return True

    async def on_help_request(self, context: str, needs_browser: bool) -> str:
        return "skip"


async def _authenticate_ws(ws: WebSocket):
    """Authenticate WebSocket via session cookie."""
    from lazyclaw.gateway.auth import get_session_user

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
    agent = Agent(_config, router, registry, permission_checker=checker)
    agent.cancel_token = cb.cancel_token
    return await agent.process_message(
        user_id, content, callback=cb, chat_session_id=session_id,
    )


@ws_chat_router.websocket("/ws/chat")
async def chat_websocket(ws: WebSocket):
    user = await _authenticate_ws(ws)
    if not user:
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    logger.info("WebSocket chat connected: user=%s", user.username)

    # Mutable holder — shared between reader task and writer tasks.
    state: dict = {"active": None}  # type: dict[str, WebSocketCallback | None]
    writer_tasks: set = set()

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

    # Forward background-task completion events so the web chat can show
    # results from tasks that started in an earlier turn (the original
    # WebSocketCallback is long gone by the time a background task finishes).
    async def _task_event_pump() -> None:
        from lazyclaw.runtime import task_event_bus

        # Initial paint: replay recent completions (last 10 min) so a user
        # who reconnects right after a task finished still sees the result.
        try:
            for evt in task_event_bus.recent_events(user.id, limit=3, max_age_s=600):
                payload = {"type": evt.kind, **evt.to_frame()}
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(payload)
        except Exception:
            logger.debug("Initial task-event paint failed", exc_info=True)
        try:
            async for evt in task_event_bus.subscribe(user.id):
                if ws.client_state != WebSocketState.CONNECTED:
                    return
                try:
                    await ws.send_json({"type": evt.kind, **evt.to_frame()})
                except Exception:
                    logger.debug("task_event send failed", exc_info=True)
                    return
        except asyncio.CancelledError:
            pass

    task_bus_task = asyncio.create_task(_task_event_pump())

    async def _maybe_suggest_template(
        user_id: str, turn_start_ts: float, cb: "WebSocketCallback"
    ) -> None:
        """Post-turn hook — emit a template_suggest frame when the user just
        ran a multi-step browser flow we could save as a reusable recipe.

        UI-only: the frame never re-enters the LLM context. Strict trigger
        (≥3 actions + ≥1 checkpoint + no existing template for the host)
        keeps noise down.
        """
        from urllib.parse import urlparse

        from lazyclaw.browser import event_bus
        from lazyclaw.browser import templates as tpl_store

        # Only events from this turn — avoid ring-buffer bleed-through.
        import time as _time
        max_age = max(1.0, _time.time() - turn_start_ts)
        events = event_bus.recent_events(user_id, limit=30, max_age_s=max_age)
        if not events:
            return

        action_events = [
            e for e in events
            if e.kind in ("action", "navigate")
            and getattr(e, "action", None) in ("click", "type", "goto", "scroll", "press_key")
        ]
        checkpoint_events = [e for e in events if e.kind == "checkpoint"]

        if len(action_events) < 3 or len(checkpoint_events) < 1:
            return

        setup_urls: list[str] = []
        seen_urls: set[str] = set()
        for e in events:
            if getattr(e, "action", None) == "goto" and e.url and e.url not in seen_urls:
                setup_urls.append(e.url)
                seen_urls.add(e.url)
            if len(setup_urls) >= 3:
                break
        if not setup_urls:
            return

        # Skip if the user already has a template covering this host.
        try:
            first_host = urlparse(setup_urls[0]).netloc.lower()
        except ValueError:
            first_host = ""
        try:
            existing = await tpl_store.list_templates(_config, user_id)
        except Exception:
            existing = []
        for t in existing:
            for u in (t.get("setup_urls") or []):
                try:
                    if urlparse(u).netloc.lower() == first_host and first_host:
                        return
                except ValueError:
                    continue

        checkpoint_names: list[str] = []
        seen_cp: set[str] = set()
        for e in checkpoint_events:
            label = getattr(e, "target", None) or getattr(e, "detail", None)
            if label and label not in seen_cp:
                checkpoint_names.append(label)
                seen_cp.add(label)

        # Suggested name — prefer a page title, fall back to the host.
        suggested_name = ""
        for e in reversed(events):
            if getattr(e, "title", None):
                suggested_name = e.title[:60].strip()
                break
        if not suggested_name:
            suggested_name = (first_host or "Saved flow")[:60]

        await cb._send({
            "type": "template_suggest",
            "suggested_name": suggested_name,
            "setup_urls": setup_urls,
            "checkpoints": checkpoint_names,
            "action_count": len(action_events),
        })

    async def _run_one_turn(content: str, session_id: str | None) -> None:
        """Run a single agent turn as its own task. Shared state['active']
        points at the current callback so the reader can route side-notes
        and cancels to it."""
        import time as _time

        cb = WebSocketCallback(ws=ws)
        state["active"] = cb
        turn_start_ts = _time.time()
        try:
            result = await _run_agent_turn(user.id, content, session_id, cb)
            # Auto-suggest template if the turn included a multi-step
            # browser flow. UI-only frame — never enters LLM context.
            try:
                await _maybe_suggest_template(user.id, turn_start_ts, cb)
            except Exception:
                logger.debug("template_suggest emit failed", exc_info=True)
            done_payload: dict = {
                "type": "done",
                "content": result or cb._buffer,
            }
            if cb._work_summary:
                done_payload["usage"] = cb._work_summary
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
                # remember which button to press.
                cb = state.get("active")
                if cb is not None:
                    cb.push_side_note(content)
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
        cb = state.get("active")
        if cb is not None:
            cb.cancel_token.cancel()
        for t in writer_tasks:
            t.cancel()
        bus_task.cancel()
        task_bus_task.cancel()
    except Exception as exc:
        logger.error("WebSocket unexpected error: %s", exc, exc_info=True)
        cb = state.get("active")
        if cb is not None:
            cb.cancel_token.cancel()
        for t in writer_tasks:
            t.cancel()
        bus_task.cancel()
        task_bus_task.cancel()
