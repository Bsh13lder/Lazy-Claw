from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWED_MCP_COMMANDS = frozenset({
    "python", "python3", "node", "npx", "uvx", "docker",
})


def _get_child_pids(parent_pid: int) -> set[int]:
    """Get PIDs of direct child processes (macOS/Linux)."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(parent_pid)],
            text=True, timeout=2,
        )
        return {int(line) for line in out.strip().split("\n") if line.strip()}
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return set()


class MCPClient:
    """Client that connects to an external MCP server and discovers its tools."""

    def __init__(
        self,
        server_id: str,
        name: str,
        transport: str,
        config: dict[str, Any],
    ) -> None:
        self._server_id = server_id
        self._name = name
        self._transport = transport
        self._config = config
        self._session: Any | None = None
        self._read_stream: Any | None = None
        self._write_stream: Any | None = None
        self._transport_ctx: Any | None = None
        self._session_ctx: Any | None = None
        self._child_process: Any | None = None  # Track stdio subprocess for cleanup
        self._child_pid: int | None = None  # PID of child process (robust tracking)
        self._stderr_log: Any | None = None  # MCP stderr log file handle

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """Establish connection to the MCP server based on transport type."""
        if self._session is not None:
            logger.warning("MCPClient %s already connected", self._server_id)
            return

        try:
            from mcp.client.session import ClientSession
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP support. "
                "Install it with: pip install mcp"
            ) from exc

        read_stream, write_stream = await self._open_transport()
        self._read_stream = read_stream
        self._write_stream = write_stream

        try:
            self._session_ctx = ClientSession(read_stream, write_stream)
            self._session = await self._session_ctx.__aenter__()
            await self._session.initialize()
            logger.info(
                "MCPClient %s connected via %s", self._server_id, self._transport
            )
        except Exception:
            await self._close_transport()
            self._session = None
            self._session_ctx = None
            raise

    async def disconnect(self) -> None:
        """Clean shutdown of session and transport."""
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("Error closing MCP session: %s", exc)
            self._session = None
            self._session_ctx = None

        await self._close_transport()
        logger.info("MCPClient %s disconnected", self._server_id)

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover tools from the MCP server.

        Returns a list of dicts with keys: name, description, inputSchema.
        """
        self._require_session()
        result = await self._session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool on the MCP server and return the result as a string."""
        self._require_session()
        logger.debug("MCPClient %s calling tool %s", self._server_id, name)
        result = await self._session.call_tool(name, arguments)

        # Concatenate text content blocks into a single string
        parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_session(self) -> None:
        if self._session is None:
            raise RuntimeError(
                f"MCPClient {self._server_id} is not connected. Call connect() first."
            )

    async def _open_transport(self) -> tuple[Any, Any]:
        """Open the transport and return (read_stream, write_stream)."""
        if self._transport == "stdio":
            return await self._open_stdio()
        if self._transport == "sse":
            return await self._open_sse()
        if self._transport == "streamable_http":
            return await self._open_streamable_http()
        raise ValueError(f"Unsupported MCP transport: {self._transport}")

    async def _open_stdio(self) -> tuple[Any, Any]:
        try:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP stdio transport. "
                "Install it with: pip install mcp"
            ) from exc

        # Build env with LOG_LEVEL=ERROR to suppress child MCP server
        # INFO spam (e.g. "Processing request of type ListToolsRequest")
        import os

        child_env = dict(os.environ, **(self._config.get("env") or {}))
        # Suppress child MCP server INFO spam on stderr
        child_env["LOG_LEVEL"] = "ERROR"
        child_env["LOGLEVEL"] = "ERROR"
        child_env["MCP_LOG_LEVEL"] = "ERROR"

        command = self._config["command"]
        base_cmd = os.path.basename(command)
        is_current_python = (
            command == sys.executable
            or os.path.realpath(command) == os.path.realpath(sys.executable)
        )
        if not is_current_python and base_cmd not in _ALLOWED_MCP_COMMANDS:
            raise ValueError(
                f"MCP command '{command}' is not allowed. "
                f"Allowed: {', '.join(sorted(_ALLOWED_MCP_COMMANDS))}, "
                f"or the current Python interpreter."
            )

        params = StdioServerParameters(
            command=command,
            args=self._config.get("args", []),
            env=child_env,
        )
        # Log MCP stderr to file instead of suppressing it
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
        os.makedirs(log_dir, exist_ok=True)
        safe_name = self._name.replace("/", "_").replace(" ", "_")
        self._stderr_log = open(os.path.join(log_dir, f"mcp-{safe_name}.stderr.log"), "a")
        self._transport_ctx = stdio_client(params, errlog=self._stderr_log)

        # Track child PIDs before/after __aenter__ to find the spawned process
        my_pid = os.getpid()
        pids_before = _get_child_pids(my_pid)
        read_stream, write_stream = await self._transport_ctx.__aenter__()
        pids_after = _get_child_pids(my_pid)

        new_pids = pids_after - pids_before
        if new_pids:
            self._child_pid = new_pids.pop()
            logger.debug("MCP %s: tracked child PID %d", self._name, self._child_pid)

        return read_stream, write_stream

    async def _open_sse(self) -> tuple[Any, Any]:
        try:
            from mcp.client.sse import sse_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP SSE transport. "
                "Install it with: pip install mcp"
            ) from exc

        url = self._config["url"]
        headers = self._config.get("headers", {})
        self._transport_ctx = sse_client(url=url, headers=headers)
        read_stream, write_stream = await self._transport_ctx.__aenter__()
        return read_stream, write_stream

    async def _open_streamable_http(self) -> tuple[Any, Any]:
        try:
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise ImportError(
                "The 'mcp' package is required for MCP streamable HTTP transport. "
                "Install it with: pip install mcp"
            ) from exc

        url = self._config["url"]
        headers = self._config.get("headers", {})
        self._transport_ctx = streamablehttp_client(url=url, headers=headers)
        result = await self._transport_ctx.__aenter__()
        # streamablehttp_client returns (read, write, session_id) — unpack first two
        read_stream, write_stream = result[0], result[1]
        return read_stream, write_stream

    async def _close_transport(self) -> None:
        if self._transport_ctx is not None:
            try:
                await self._transport_ctx.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("Error closing MCP transport: %s", exc)
            self._transport_ctx = None
            self._read_stream = None
            self._write_stream = None
        if self._stderr_log is not None:
            self._stderr_log.close()
            self._stderr_log = None

        # Force-kill the child process if transport cleanup missed it
        # (prevents zombie processes — root cause of 100+ orphaned MCPs)
        proc = self._child_process
        self._child_process = None
        if proc is not None:
            try:
                if proc.returncode is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()
                    logger.debug("Force-killed MCP child process (pid=%s)", proc.pid)
            except Exception as exc:
                logger.debug("Error killing MCP child process: %s", exc)

        # PID-based fallback: kill child by PID if process object wasn't captured
        child_pid = self._child_pid
        self._child_pid = None
        if child_pid is not None and proc is None:
            try:
                os.kill(child_pid, 0)  # Check if alive
                import signal
                os.kill(child_pid, signal.SIGTERM)
                logger.debug("Force-killed MCP child by PID %d", child_pid)
            except OSError:
                pass  # Already dead — good
