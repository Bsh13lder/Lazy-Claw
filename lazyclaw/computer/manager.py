"""Computer manager — unified facade for local and remote computer control.

Routes commands to either NativeExecutor (local subprocess) or ConnectorServer
(remote WebSocket relay) based on whether the user has a connected connector.
"""
from __future__ import annotations

from lazyclaw.computer.connector_server import ConnectorServer
from lazyclaw.computer.native import NativeExecutor
from lazyclaw.computer.security import SecurityManager
from lazyclaw.config import Config


class ComputerManager:
    """Unified interface for computer control (local or remote)."""

    def __init__(self, config: Config, connector_server: ConnectorServer) -> None:
        self._config = config
        self._connector = connector_server
        self._security = SecurityManager()
        self._native = NativeExecutor(self._security)

    async def _execute_remote(
        self, user_id: str, command: str, args: dict, timeout: int = 35
    ) -> dict:
        """Send command to remote connector and wait for result."""
        try:
            cmd_id = await self._connector.send_command(user_id, command, args)
            result = await self._connector.wait_for_result(cmd_id, timeout=timeout)
            if result is None:
                return {"success": False, "error": "Command timed out (remote)"}
            return result
        except ConnectionError as e:
            return {"success": False, "error": str(e)}

    def _is_remote(self, user_id: str) -> bool:
        """Check if user has a connected remote connector."""
        return self._connector.is_connected(user_id)

    async def exec_command(
        self, user_id: str, cmd: str, timeout: int = 30
    ) -> dict:
        """Execute a shell command (routes to remote if connected)."""
        if self._is_remote(user_id):
            return await self._execute_remote(
                user_id, "exec", {"cmd": cmd}, timeout=timeout + 5
            )
        return await self._native.exec_command(cmd, timeout=timeout)

    async def read_file(self, user_id: str, path: str) -> dict:
        """Read a file."""
        if self._is_remote(user_id):
            return await self._execute_remote(
                user_id, "read_file", {"path": path}
            )
        return await self._native.read_file(path)

    async def write_file(self, user_id: str, path: str, content: str) -> dict:
        """Write content to a file."""
        if self._is_remote(user_id):
            return await self._execute_remote(
                user_id, "write_file", {"path": path, "content": content}
            )
        return await self._native.write_file(path, content)

    async def list_dir(self, user_id: str, path: str | None = None) -> dict:
        """List directory contents."""
        if self._is_remote(user_id):
            return await self._execute_remote(
                user_id, "list_dir", {"path": path or ""}
            )
        return await self._native.list_dir(path)

    async def screenshot(self, user_id: str) -> dict:
        """Capture a screenshot."""
        if self._is_remote(user_id):
            return await self._execute_remote(
                user_id, "screenshot", {}
            )
        return await self._native.screenshot()
