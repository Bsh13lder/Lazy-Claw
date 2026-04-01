from __future__ import annotations

import asyncio
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
    """Client that connects to an external MCP server and discovers its tools.

    Uses a dedicated background task to hold transport context managers open,
    ensuring __aenter__/__aexit__ happen in the same anyio task (required by
    anyio's cancel scope rules for task groups inside stdio_client).
    """

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
        self._child_process: Any | None = None  # Track stdio subprocess for cleanup
        self._child_pid: int | None = None  # PID of child process (robust tracking)
        self._stderr_log: Any | None = None  # MCP stderr log file handle

        # Background task that owns the transport + session context managers
        self._ctx_task: asyncio.Task | None = None
        self._ready_event: asyncio.Event | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._ctx_error: Exception | None = None

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
        """Establish connection to the MCP server based on transport type.

        Spawns a background task that holds the context managers open.
        This ensures __aenter__/__aexit__ happen in the same anyio task,
        fixing the cancel scope race in the MCP SDK.
        """
        if self._session is not None:
            logger.warning("MCPClient %s already connected", self._server_id)
            return

        self._ready_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._ctx_error = None

        self._ctx_task = asyncio.create_task(
            self._run_context(),
            name=f"mcp-ctx-{self._name}",
        )

        # Wait for the background task to finish setup
        await self._ready_event.wait()

        if self._ctx_error is not None:
            # Background task failed during setup — propagate
            err = self._ctx_error
            self._ctx_error = None
            self._ctx_task = None
            raise err

        logger.info(
            "MCPClient %s connected via %s", self._server_id, self._transport
        )

    async def _run_context(self) -> None:
        """Background task that owns transport + session context managers.

        Enters both contexts, signals ready, then waits for shutdown.
        On shutdown (or error), exits contexts in the SAME task — fixing
        the anyio cancel scope requirement.
        """
        try:
            from mcp.client.session import ClientSession
        except ImportError as exc:
            self._ctx_error = ImportError(
                "The 'mcp' package is required for MCP support. "
                "Install it with: pip install mcp"
            )
            self._ready_event.set()
            return

        transport_ctx = None
        session_ctx = None
        try:
            transport_ctx = self._create_transport_ctx()
            if transport_ctx is None:
                self._ctx_error = ValueError(
                    f"Failed to create transport for {self._transport}"
                )
                self._ready_event.set()
                return

            # Track child PIDs for stdio
            my_pid = os.getpid()
            pids_before = _get_child_pids(my_pid)

            result = await transport_ctx.__aenter__()

            # Handle different return types
            if isinstance(result, tuple) and len(result) >= 2:
                read_stream, write_stream = result[0], result[1]
            else:
                read_stream, write_stream = result

            pids_after = _get_child_pids(my_pid)
            new_pids = pids_after - pids_before
            if new_pids:
                self._child_pid = new_pids.pop()
                logger.debug("MCP %s: tracked child PID %d", self._name, self._child_pid)

            self._read_stream = read_stream
            self._write_stream = write_stream

            # Enter session context
            session_ctx = ClientSession(read_stream, write_stream)
            self._session = await session_ctx.__aenter__()
            await self._session.initialize()

            # Signal that connect() can return
            self._ready_event.set()

            # Hold contexts open until shutdown requested
            await self._shutdown_event.wait()

        except Exception as exc:
            if not self._ready_event.is_set():
                # Error during setup — report to connect()
                self._ctx_error = exc
                self._ready_event.set()
            else:
                logger.warning("MCP %s context task error: %s", self._name, exc)
        finally:
            # Clean up in the SAME task that entered (fixes cancel scope)
            self._session = None

            if session_ctx is not None:
                try:
                    await session_ctx.__aexit__(None, None, None)
                except Exception as exc:
                    logger.debug("Error closing MCP session: %s", exc)

            if transport_ctx is not None:
                try:
                    await transport_ctx.__aexit__(None, None, None)
                except Exception as exc:
                    logger.debug("Error closing MCP transport: %s", exc)

            self._read_stream = None
            self._write_stream = None

            # Close stderr log
            if self._stderr_log is not None:
                self._stderr_log.close()
                self._stderr_log = None

            # Force-kill child process if still alive
            self._kill_child()

    async def disconnect(self) -> None:
        """Clean shutdown of session and transport."""
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        if self._ctx_task is not None:
            try:
                await asyncio.wait_for(self._ctx_task, timeout=5)
            except asyncio.TimeoutError:
                logger.warning("MCP %s context task didn't stop in 5s, cancelling", self._name)
                self._ctx_task.cancel()
                try:
                    await self._ctx_task
                except (asyncio.CancelledError, Exception):
                    pass  # intentional: cancelled task cleanup, exception is expected
            except (asyncio.CancelledError, Exception):
                pass  # intentional: cancelled task cleanup, exception is expected
            self._ctx_task = None

        # Fallback: kill child process if context cleanup missed it
        self._kill_child()

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

        # Check MCP protocol error flag first
        if getattr(result, "isError", False):
            error_parts = [
                block.text for block in result.content
                if hasattr(block, "text")
            ]
            error_detail = "\n".join(error_parts).strip()
            if error_detail:
                return f"[MCP ERROR] The tool reported an error: {error_detail}"
            return "[MCP ERROR] The tool reported an error but provided no details."

        # Concatenate text content blocks into a single string
        parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        text = "\n".join(parts)

        # Guard against empty results that cause LLM hallucination
        if not text.strip():
            return (
                "[NO DATA] The tool returned an empty result. "
                "No information is available. Do not guess or fabricate data."
            )

        return text

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_session(self) -> None:
        if self._session is None:
            raise RuntimeError(
                f"MCPClient {self._server_id} is not connected. Call connect() first."
            )

    def _create_transport_ctx(self) -> Any | None:
        """Create the transport context manager (not entered yet)."""
        if self._transport == "stdio":
            return self._create_stdio_ctx()
        if self._transport == "sse":
            return self._create_sse_ctx()
        if self._transport == "streamable_http":
            return self._create_streamable_http_ctx()
        return None

    def _create_stdio_ctx(self) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        child_env = dict(os.environ, **(self._config.get("env") or {}))
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
        # Log MCP stderr to file
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
        )
        os.makedirs(log_dir, exist_ok=True)
        safe_name = self._name.replace("/", "_").replace(" ", "_")
        self._stderr_log = open(
            os.path.join(log_dir, f"mcp-{safe_name}.stderr.log"), "a"
        )
        return stdio_client(params, errlog=self._stderr_log)

    def _create_sse_ctx(self) -> Any:
        from mcp.client.sse import sse_client
        url = self._config["url"]
        headers = self._config.get("headers", {})
        return sse_client(url=url, headers=headers)

    def _create_streamable_http_ctx(self) -> Any:
        from mcp.client.streamable_http import streamablehttp_client
        url = self._config["url"]
        headers = self._config.get("headers", {})
        return streamablehttp_client(url=url, headers=headers)

    def _kill_child(self) -> None:
        """Force-kill the child process if still alive."""
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

        child_pid = self._child_pid
        self._child_pid = None
        if child_pid is not None and proc is None:
            try:
                os.kill(child_pid, 0)  # Check if alive
                import signal
                os.kill(child_pid, signal.SIGTERM)
                logger.debug("Force-killed MCP child by PID %d", child_pid)
            except OSError:
                pass  # Already dead
