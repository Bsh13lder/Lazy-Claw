"""Thin async CDP (Chrome DevTools Protocol) client.

Connects to a real Chrome browser via WebSocket. No heavy frameworks —
direct CDP protocol over websockets + httpx for discovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Union

import httpx

# Event handler signature: sync or async callable taking CDP event params.
EventHandler = Callable[[dict], Union[None, Awaitable[None]]]

logger = logging.getLogger(__name__)

# Default CDP debugging port
DEFAULT_CDP_PORT = 9222


@dataclass(frozen=True)
class CDPTab:
    """Immutable info about a Chrome tab from /json endpoint."""

    id: str
    title: str
    url: str
    ws_url: str
    tab_type: str  # "page", "background_page", "service_worker", etc.


async def find_chrome_cdp(port: int = DEFAULT_CDP_PORT) -> str | None:
    """Discover Chrome's CDP WebSocket URL.

    Queries http://localhost:{port}/json/version for the browser-level
    webSocketDebuggerUrl. Returns None if Chrome is not reachable.
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"http://localhost:{port}/json/version")
            if resp.status_code == 200:
                data = resp.json()
                ws_url = data.get("webSocketDebuggerUrl")
                if ws_url:
                    logger.debug("Found Chrome CDP at %s", ws_url)
                    return ws_url
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
        logger.debug("Chrome CDP not available on port %d: %s", port, exc)
    return None


async def list_chrome_tabs(port: int = DEFAULT_CDP_PORT) -> list[CDPTab]:
    """List all open Chrome tabs via the /json endpoint.

    Returns only 'page' type targets (actual tabs, not service workers).
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"http://localhost:{port}/json")
            if resp.status_code == 200:
                targets = resp.json()
                tabs = []
                for t in targets:
                    if t.get("type") == "page":
                        tabs.append(CDPTab(
                            id=t.get("id", ""),
                            title=t.get("title", ""),
                            url=t.get("url", ""),
                            ws_url=t.get("webSocketDebuggerUrl", ""),
                            tab_type=t.get("type", "page"),
                        ))
                return tabs
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
        logger.debug("Failed to list Chrome tabs: %s", exc)
    return []


class CDPConnection:
    """Low-level CDP WebSocket protocol handler.

    Sends CDP commands as JSON and awaits responses by matching request IDs.
    """

    def __init__(self) -> None:
        self._ws = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listener_task: asyncio.Task | None = None
        self._connected = False
        # CDP event subscriptions: method name -> list of handlers.
        # Events without an id (e.g. Network.requestWillBeSent) are dispatched
        # to all registered handlers; exceptions from one handler never kill
        # the listener or interfere with the others.
        self._event_handlers: dict[str, list[EventHandler]] = {}

    def register_event_handler(self, method: str, handler: EventHandler) -> None:
        """Subscribe a callback to a CDP event method.

        Multiple handlers per method are allowed. Handlers may be sync or
        async; async handlers are scheduled via asyncio.create_task so the
        listener loop is never blocked.
        """
        self._event_handlers.setdefault(method, []).append(handler)

    def clear_event_handlers(self) -> None:
        """Drop all event subscriptions — call on reconnect / tab switch."""
        self._event_handlers.clear()

    async def connect(self, ws_url: str, origin: str | None = None) -> None:
        """Connect to a CDP target via WebSocket.

        ``origin`` sets the ``Origin`` header on the handshake. Required when
        the remote Chromium was launched with ``--remote-allow-origins=<value>``
        (host-browser bridge). For connections to ``localhost`` this is almost
        always ``None`` — Chromium auto-accepts any Origin from loopback.
        """
        try:
            import websockets
        except ImportError:
            raise ImportError(
                "websockets package required for CDP. "
                "Install with: pip install websockets"
            )

        connect_kwargs: dict = {
            "max_size": 50 * 1024 * 1024,  # 50MB for large screenshots
            "ping_interval": 30,
            "ping_timeout": 10,
        }
        if origin:
            # websockets v12+ uses ``additional_headers``; older v10/11 use
            # ``extra_headers``. Try the new name first, fall back to legacy.
            header = [("Origin", origin)]
            try:
                self._ws = await websockets.connect(
                    ws_url, additional_headers=header, **connect_kwargs,
                )
            except TypeError:
                self._ws = await websockets.connect(
                    ws_url, extra_headers=header, **connect_kwargs,
                )
        else:
            self._ws = await websockets.connect(ws_url, **connect_kwargs)

        self._connected = True
        self._listener_task = asyncio.create_task(self._listen())
        logger.info("CDP connected to %s%s", ws_url, " (with Origin)" if origin else "")

    async def send(
        self,
        method: str,
        params: dict | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Send a CDP command and wait for the response.

        When session_id is provided, the command is scoped to that specific
        target session (tab). This enables flat-mode session multiplexing
        over a single WebSocket connection.
        """
        if not self._ws or not self._connected:
            raise ConnectionError("CDP not connected")

        self._msg_id += 1
        msg_id = self._msg_id
        message: dict = {"id": msg_id, "method": method}
        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        await self._ws.send(json.dumps(message))

        try:
            result = await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"CDP command timed out: {method}")

        if "error" in result:
            err = result["error"]
            raise RuntimeError(
                f"CDP error ({err.get('code', '?')}): {err.get('message', '?')}"
            )

        return result.get("result", {})

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                # Intentional: task was cancelled as part of close()
                pass
            self._listener_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                logger.warning("Failed to cleanly close CDP WebSocket", exc_info=True)
            self._ws = None

        # Resolve any pending futures with error
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("CDP disconnected"))
        self._pending.clear()

        logger.info("CDP connection closed")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    async def _listen(self) -> None:
        """Background task: read WebSocket messages and route responses."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Received non-JSON CDP message, skipping", exc_info=True)
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                    continue

                # Event dispatch. Method-less messages get dropped silently.
                method = msg.get("method")
                if not method:
                    continue
                handlers = self._event_handlers.get(method)
                if not handlers:
                    continue
                params = msg.get("params", {}) or {}
                for cb in handlers:
                    try:
                        result = cb(params)
                        if asyncio.iscoroutine(result):
                            asyncio.create_task(result)
                    except Exception:
                        logger.debug(
                            "CDP event handler for %s raised (non-fatal)",
                            method, exc_info=True,
                        )

        except asyncio.CancelledError:
            # Intentional: listener task cancelled during close()
            pass
        except Exception as exc:
            logger.debug("CDP listener error: %s", exc)
            self._connected = False
