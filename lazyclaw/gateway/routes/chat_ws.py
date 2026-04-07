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

        elif kind == "work_summary":
            self._work_summary = event.metadata.get("summary")

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


@ws_chat_router.websocket("/ws/chat")
async def chat_websocket(ws: WebSocket):
    user = await _authenticate_ws(ws)
    if not user:
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    logger.info("WebSocket chat connected: user=%s", user.username)

    active_callback: WebSocketCallback | None = None

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "cancel":
                if active_callback:
                    active_callback.cancel_token.cancel()

            elif msg_type == "message":
                content = data.get("content", "").strip()
                if not content:
                    await ws.send_json({"type": "error", "message": "Empty message"})
                    continue

                session_id = data.get("session_id")
                active_callback = WebSocketCallback(ws=ws)

                try:
                    if _lane_queue:
                        result = await _lane_queue.enqueue(
                            user.id, content,
                            callback=active_callback,
                            chat_session_id=session_id,
                        )
                    else:
                        from lazyclaw.llm.router import LLMRouter
                        from lazyclaw.permissions.checker import PermissionChecker
                        from lazyclaw.runtime.agent import Agent
                        from lazyclaw.skills.registry import SkillRegistry

                        registry = _shared_registry or SkillRegistry()
                        if not _shared_registry:
                            registry.register_defaults(config=_config)
                        router = LLMRouter(_config)
                        checker = PermissionChecker(_config, registry)
                        agent = Agent(
                            _config, router, registry,
                            permission_checker=checker,
                        )
                        agent.cancel_token = active_callback.cancel_token
                        result = await agent.process_message(
                            user.id, content,
                            callback=active_callback,
                            chat_session_id=session_id,
                        )

                    done_payload: dict = {
                        "type": "done",
                        "content": result or active_callback._buffer,
                    }
                    if active_callback._work_summary:
                        done_payload["usage"] = active_callback._work_summary
                    await active_callback._send(done_payload)
                except asyncio.CancelledError:
                    await active_callback._send({"type": "cancelled"})
                except Exception as exc:
                    logger.error("WebSocket chat error: %s", exc, exc_info=True)
                    await active_callback._send({
                        "type": "error",
                        "message": str(exc),
                    })
                finally:
                    active_callback = None

    except WebSocketDisconnect:
        logger.info("WebSocket chat disconnected: user=%s", user.username)
        if active_callback:
            active_callback.cancel_token.cancel()
    except Exception as exc:
        logger.error("WebSocket unexpected error: %s", exc, exc_info=True)
        if active_callback:
            active_callback.cancel_token.cancel()
