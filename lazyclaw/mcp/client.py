from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWED_MCP_COMMANDS = frozenset({
    "python", "python3", "node", "npx", "uvx", "docker",
    # pip console-script entry points for bundled MCPs
    "workspace-mcp",
})

# Bounded respawn policy — matches Claude Code's own MCP reconnect
# (5 attempts, 1→2→4→8→16s) and Google ADK's lazy-reconnect pattern.
# Without bounds an unrecoverable child (binary missing, port permanently
# taken) would loop forever and burn the lane queue.
_MAX_RESPAWN_ATTEMPTS = 5
_RESPAWN_BASE_DELAY = 1.0
_RESPAWN_MAX_DELAY = 16.0
# After this much quiet time since the last respawn, reset the attempt
# counter so a brand-new outage gets a fresh budget.
_RESPAWN_RESET_AFTER = 300.0
# Pre-rotate the stderr log when it crosses this size at open time.
# crawl4ai children are chatty; without this the log fills the disk.
_STDERR_LOG_MAX_BYTES = 50 * 1024 * 1024


def _is_transport_error(exc: BaseException) -> bool:
    """True for exceptions that mean "stdio pipe is dead, the server is gone".

    We match by class name to avoid importing anyio here (it's a transitive
    dep of mcp, not a direct dep of lazyclaw). The set covers what Google ADK,
    Grinta, and EasyMCP all catch in their own respawn paths.
    """
    name = type(exc).__name__
    if name in ("BrokenResourceError", "ClosedResourceError", "EndOfStream"):
        return True
    if isinstance(exc, (BrokenPipeError, ConnectionError, OSError)):
        return True
    # The mcp SDK's send_request raises a bare RuntimeError when the
    # response stream is closed mid-flight (mcp/shared/session.py).
    if isinstance(exc, RuntimeError) and "closed" in str(exc).lower():
        return True
    return False


def _get_child_pids(parent_pid: int) -> set[int]:
    """Get PIDs of direct child processes (macOS/Linux).

    Slim container images don't ship procps, so pgrep won't be on PATH.
    Treat "missing binary" as "no children" silently — emitting a stack
    trace every connection just spams the logs. Other failures still log
    at DEBUG with context.
    """
    import shutil

    if not shutil.which("pgrep"):
        return set()
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(parent_pid)],
            text=True, timeout=2,
        )
        return {int(line) for line in out.strip().split("\n") if line.strip()}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return set()
    except FileNotFoundError:
        # Race: pgrep removed between which() and exec(). Treat as no children.
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
        # Per-instance lock around schema-cache-touching paths only
        # (list_tools). NOT used around call_tool: the MCP SDK's
        # send_request keys responses by request_id (mcp/shared/session.py
        # line 256-308), and the stdio transport serializes wire writes
        # via a single stdin_writer task that pumps an anyio
        # MemoryObjectStream (mcp/client/stdio/__init__.py line 166).
        # Concurrent call_tool invocations are therefore safe at the
        # protocol layer; throttling lives in
        # bridge._get_call_semaphore (per-server semaphore).
        self._call_lock = asyncio.Lock()
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

        # Respawn state — guarded by _respawn_lock so 9 parallel callers all
        # hitting a dead pipe at once trigger exactly one teardown+reconnect.
        self._respawn_lock = asyncio.Lock()
        self._respawn_attempts = 0
        self._last_respawn_at = 0.0
        self._dead = False

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def is_alive(self) -> bool:
        """Stricter than is_connected — also rejects the "subprocess died but
        _run_context.finally hasn't fired yet" window where _session is still
        set but the underlying streams are closed.

        Mirrors Google ADK's _is_session_disconnected (stream `_closed` probe)
        plus a check that the context-holder task is still running.
        """
        if self._dead or self._session is None:
            return False
        task = self._ctx_task
        if task is None or task.done():
            return False
        for stream in (self._read_stream, self._write_stream):
            if stream is not None and getattr(stream, "_closed", False):
                return False
        return True

    @property
    def is_dead(self) -> bool:
        """Permanently dead — respawn budget exhausted, don't try again."""
        return self._dead

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
        async with self._call_lock:
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
        """Invoke a tool on the MCP server and return the result as a string.

        On transport-level failure (broken pipe, closed stream, dead
        subprocess) respawns the child and retries the call exactly once.
        Bounded by ``_MAX_RESPAWN_ATTEMPTS`` per ``_RESPAWN_RESET_AFTER``
        window so an unrecoverable server can't lock the lane queue.
        """
        self._require_session()
        logger.debug("MCPClient %s calling tool %s", self._server_id, name)
        try:
            return await self._call_tool_once(name, arguments)
        except Exception as exc:
            if not _is_transport_error(exc):
                raise
            logger.warning(
                "MCP %s transport error on %s (%s) — respawning",
                self._name, name, type(exc).__name__,
            )
            if not await self.respawn():
                raise RuntimeError(
                    f"MCP {self._name} subprocess died and respawn failed: {exc}"
                ) from exc
            # Fresh subprocess + session — retry once. A second transport
            # error here propagates so the bridge layer can surface it.
            return await self._call_tool_once(name, arguments)

    async def _call_tool_once(self, name: str, arguments: dict[str, Any]) -> str:
        """Single call_tool attempt with no respawn. Used by call_tool wrapper."""
        # No lock: the MCP SDK already demultiplexes concurrent requests
        # by id (mcp/shared/session.py:_response_streams) and the stdio
        # transport's single writer task drains anyio.MemoryObjectStream
        # messages serially. Holding a lock here would queue every parallel
        # caller behind the slowest scrape — that was the 9-subagent
        # salon-batch timeout. Throttle via bridge._get_call_semaphore.
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

        # FastMCP serializes ``list[dict]`` tool returns as ONE TextContent
        # block per list item — naively joining with "\n" produces N concat
        # JSON objects with NO outer array brackets:
        #     {"a":1}
        #     {"b":2}
        # That is not valid JSON, json.loads fails on the second `{`, and
        # callers (e.g. survival.search_skill) log "Upwork MCP returned
        # non-JSON: {" and fall back to web_search / browser specialists —
        # 5+ minute round-trips for what should have been a 1-call lookup.
        # When every block parses individually as JSON, wrap the whole thing
        # in `[...]` and emit valid JSON-array text.
        if len(parts) >= 2:
            import json as _json
            try:
                _objs = [_json.loads(p) for p in parts if p.strip()]
                if _objs:
                    return _json.dumps(_objs)
            except (_json.JSONDecodeError, ValueError):
                # At least one part isn't standalone JSON — fall through to
                # the original "\n"-join behavior (preserves human-readable
                # multi-line text outputs).
                pass

        text = "\n".join(parts)

        # Guard against empty results that cause LLM hallucination
        if not text.strip():
            return (
                "[NO DATA] The tool returned an empty result. "
                "No information is available. Do not guess or fabricate data."
            )

        return text

    async def respawn(self) -> bool:
        """Tear down the current transport+session and spawn a fresh one.

        Returns True on successful reconnect, False when the attempt budget
        is exhausted or the underlying connect() fails. The lock collapses
        the thundering-herd case where N parallel callers all see a dead
        pipe at the same moment — only one teardown runs; the rest see an
        already-alive client when the lock releases and skip the work.

        Always allocates a fresh transport context (Continue.dev #11886:
        reusing a Client instance after close() throws "Already connected to
        a transport" because _transport is cleared async by _onclose).
        """
        if self._dead:
            return False

        async with self._respawn_lock:
            # Another caller may have just respawned us — short-circuit.
            if self.is_alive:
                return True

            now = time.monotonic()
            if now - self._last_respawn_at > _RESPAWN_RESET_AFTER:
                self._respawn_attempts = 0

            if self._respawn_attempts >= _MAX_RESPAWN_ATTEMPTS:
                self._dead = True
                logger.error(
                    "MCP %s exhausted %d respawn attempts — marking dead",
                    self._name, _MAX_RESPAWN_ATTEMPTS,
                )
                return False

            attempt = self._respawn_attempts
            self._respawn_attempts += 1
            self._last_respawn_at = now

            delay = min(_RESPAWN_BASE_DELAY * (2 ** attempt), _RESPAWN_MAX_DELAY)
            logger.warning(
                "MCP %s respawning (attempt %d/%d, delay %.1fs)",
                self._name, attempt + 1, _MAX_RESPAWN_ATTEMPTS, delay,
            )

            # Tear down the old transport+session in the same task that
            # entered them — disconnect()'s shutdown_event triggers
            # _run_context's finally where the cancel-scope-safe cleanup
            # happens. Don't try to swap streams in-place.
            try:
                await self.disconnect()
            except Exception as exc:
                logger.debug(
                    "MCP %s disconnect raised during respawn: %s",
                    self._name, exc,
                )

            await asyncio.sleep(delay)

            try:
                await self.connect()
            except Exception as exc:
                logger.warning(
                    "MCP %s respawn connect failed (attempt %d/%d): %s",
                    self._name, attempt + 1, _MAX_RESPAWN_ATTEMPTS, exc,
                )
                # If the failing attempt was the last one in the budget,
                # mark dead now so future callers short-circuit instead of
                # also taking the lock and discovering they're past the cap.
                if self._respawn_attempts >= _MAX_RESPAWN_ATTEMPTS:
                    self._dead = True
                    logger.error(
                        "MCP %s exhausted %d respawn attempts — marking dead",
                        self._name, _MAX_RESPAWN_ATTEMPTS,
                    )
                return False

            self._respawn_attempts = 0
            logger.info("MCP %s respawned successfully", self._name)
            return True

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
        # If stored command is a python interpreter that no longer exists
        # (e.g. registered on macOS, now running in Docker), use current one.
        # Pairs with manager._canonical_bundled_config, which runs earlier (at
        # connect) and also covers a stale command that still EXISTS — this
        # one only fires when the path is gone.
        if not is_current_python and base_cmd.startswith("python") and not os.path.isfile(command):
            command = sys.executable
            is_current_python = True
        if not is_current_python and base_cmd not in _ALLOWED_MCP_COMMANDS:
            raise ValueError(
                f"MCP command '{command}' is not allowed. "
                f"Allowed: {', '.join(sorted(_ALLOWED_MCP_COMMANDS))}, "
                f"or the current Python interpreter."
            )

        # Fix stale file paths in args (e.g. node script registered on macOS,
        # now running in Docker). Re-resolve relative to current project root.
        args = list(self._config.get("args", []))
        for i, arg in enumerate(args):
            if arg.endswith(".js") and not os.path.isfile(arg):
                # Try resolving from project root (lazyclaw/mcp/../../)
                from lazyclaw.mcp.manager import BUNDLED_MCPS
                for info in BUNDLED_MCPS.values():
                    if "node" in info and os.path.basename(arg) == os.path.basename(info["node"]):
                        resolved = os.path.abspath(os.path.join(
                            os.path.dirname(__file__), "..", "..", info["node"],
                        ))
                        if os.path.isfile(resolved):
                            args[i] = resolved
                        break

        # Per-server working directory. Used by claude-code so the agentic
        # write→run→test loop writes files into the persistent
        # /workspace bind mount instead of /app (which would vanish on
        # container rebuild). Optional — when unset the subprocess
        # inherits LazyClaw's cwd, matching the historical default.
        # Auto-create the dir on first connect so the bind mount works
        # cleanly on a fresh host machine.
        cwd = self._config.get("cwd")
        if cwd:
            try:
                os.makedirs(cwd, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "MCP %s: failed to ensure cwd=%s exists (%s) — "
                    "falling back to inherited cwd",
                    self._name, cwd, exc,
                )
                cwd = None

        params = StdioServerParameters(
            command=command,
            args=args,
            env=child_env,
            cwd=cwd,
        )
        # Log MCP stderr to file. Pre-rotate when the file gets large so a
        # chatty child (crawl4ai etc.) can't fill the disk between restarts;
        # write a banner per generation so post-mortems can correlate stderr
        # output with the respawn attempt that produced it.
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
        )
        os.makedirs(log_dir, exist_ok=True)
        safe_name = self._name.replace("/", "_").replace(" ", "_")
        log_path = os.path.join(log_dir, f"mcp-{safe_name}.stderr.log")
        try:
            if os.path.getsize(log_path) > _STDERR_LOG_MAX_BYTES:
                rotated = log_path + ".1"
                try:
                    os.replace(log_path, rotated)
                except OSError as exc:
                    logger.debug("MCP %s stderr rotate failed: %s", self._name, exc)
        except FileNotFoundError:
            pass
        self._stderr_log = open(log_path, "a")
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            attempt = self._respawn_attempts
            banner = f"\n--- MCP {self._name} start ts={ts} attempt={attempt} ---\n"
            self._stderr_log.write(banner)
            self._stderr_log.flush()
        except Exception:
            logger.debug("MCP %s stderr banner write failed", self._name, exc_info=True)
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
                        logger.debug("MCP child process did not exit in 3s, force-killing (pid=%s)", proc.pid, exc_info=True)
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
                logger.debug("MCP child process PID %d already dead", child_pid)
