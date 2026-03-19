from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
        child_env.setdefault("LOG_LEVEL", "ERROR")

        params = StdioServerParameters(
            command=self._config["command"],
            args=self._config.get("args", []),
            env=child_env,
        )
        self._transport_ctx = stdio_client(params)
        read_stream, write_stream = await self._transport_ctx.__aenter__()
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
        self._transport_ctx = streamablehttp_client(url=url)
        read_stream, write_stream = await self._transport_ctx.__aenter__()
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
