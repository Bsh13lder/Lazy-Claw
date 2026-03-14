"""Connector server — server-side WebSocket manager for remote computer connectors.

Manages one WebSocket connection per user. Relays commands from skills/agent
to the user's connector and returns results via asyncio Events.
Adapted from LazyTasker bot/connector_manager.py.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


class ConnectorServer:
    """Manages WebSocket connections from desktop connectors."""

    def __init__(self, config: Config) -> None:
        self._config = config
        # user_id -> WebSocket connection
        self._connections: dict[str, Any] = {}
        # user_id -> device info
        self._device_info: dict[str, dict] = {}
        # command_id -> asyncio.Event
        self._command_events: dict[str, asyncio.Event] = {}
        # command_id -> result data
        self._command_responses: dict[str, dict] = {}

    # ==================== Token Management ====================

    async def create_token(self, user_id: str) -> str:
        """Generate a new connector token for a user.

        Replaces any existing token for this user.
        """
        token = str(uuid.uuid4())
        token_id = str(uuid.uuid4())[:8]

        async with db_session(self._config) as db:
            await db.execute(
                "DELETE FROM connector_tokens WHERE user_id = ?", (user_id,)
            )
            await db.execute(
                "INSERT INTO connector_tokens (id, user_id, token) VALUES (?, ?, ?)",
                (token_id, user_id, token),
            )
            await db.commit()

        return token

    async def validate_token(self, token: str) -> str | None:
        """Validate a connector token and return the user_id, or None."""
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT user_id FROM connector_tokens WHERE token = ?", (token,)
            )
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    "UPDATE connector_tokens SET last_used = datetime('now') "
                    "WHERE token = ?",
                    (token,),
                )
                await db.commit()
                return row[0]
        return None

    async def delete_token(self, user_id: str) -> bool:
        """Delete a user's connector token."""
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "DELETE FROM connector_tokens WHERE user_id = ?", (user_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ==================== Connection Management ====================

    def register(self, user_id: str, websocket: Any, device_info: dict) -> None:
        """Register a connector WebSocket connection."""
        old = self._connections.get(user_id)
        if old:
            logger.info(f"Replacing existing connector for user {user_id}")

        self._connections[user_id] = websocket
        self._device_info[user_id] = device_info
        logger.info(
            f"Connector registered: user={user_id}, "
            f"platform={device_info.get('platform')}, "
            f"hostname={device_info.get('hostname')}"
        )

    def unregister(self, user_id: str) -> None:
        """Remove a connector connection and clean up pending commands."""
        self._connections.pop(user_id, None)
        self._device_info.pop(user_id, None)
        logger.info(f"Connector unregistered: user={user_id}")

        # Resolve any pending command events with error
        stale = [
            cid for cid, evt in self._command_events.items()
            if not evt.is_set()
        ]
        for cid in stale:
            self._command_responses[cid] = {
                "success": False, "error": "Connector disconnected"
            }
            evt = self._command_events.get(cid)
            if evt:
                evt.set()

    def is_connected(self, user_id: str) -> bool:
        """Check if a user's connector is online."""
        ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            if hasattr(ws, 'client_state'):
                from starlette.websockets import WebSocketState
                return ws.client_state == WebSocketState.CONNECTED
            return True
        except Exception:
            return True

    def get_device_info(self, user_id: str) -> dict | None:
        """Get device info for a connected user."""
        return self._device_info.get(user_id)

    # ==================== Command Relay ====================

    async def send_command(
        self, user_id: str, command: str, args: dict
    ) -> str:
        """Send a command to the user's connector.

        Returns a command_id to wait on.
        """
        ws = self._connections.get(user_id)
        if not ws:
            raise ConnectionError("Connector not connected")

        cmd_id = str(uuid.uuid4())[:8]
        event = asyncio.Event()
        self._command_events[cmd_id] = event

        msg = {
            "type": "command",
            "id": cmd_id,
            "command": command,
            "args": args,
        }

        try:
            await ws.send_json(msg)
        except Exception as e:
            self._command_events.pop(cmd_id, None)
            raise ConnectionError(f"Failed to send command: {e}")

        return cmd_id

    async def wait_for_result(
        self, command_id: str, timeout: float = 35
    ) -> dict | None:
        """Wait for a command result. Returns result dict or None on timeout."""
        event = self._command_events.get(command_id)
        if not event:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._command_responses.pop(command_id, None)
        except asyncio.TimeoutError:
            return None
        finally:
            self._command_events.pop(command_id, None)

    def report_result(self, command_id: str, result: dict) -> None:
        """Called when connector sends a result back."""
        self._command_responses[command_id] = result
        event = self._command_events.get(command_id)
        if event:
            event.set()
