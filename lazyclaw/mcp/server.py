from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette

    from lazyclaw.config import Config
    from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

MCP_SERVER_USER_ID = "mcp-client"


def create_mcp_server(registry: SkillRegistry, config: Config) -> Server:  # type: ignore[name-defined]  # noqa: F821
    """Create an MCP Server that exposes LazyClaw skills as MCP tools.

    All skills in the registry are listed as MCP tools. Tool calls are
    dispatched to the skill's execute method with a fixed user_id.
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server("lazyclaw")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = []
        for tool_def in registry.list_tools():
            fn = tool_def["function"]
            tools.append(
                Tool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    inputSchema=fn.get("parameters", {"type": "object", "properties": {}}),
                )
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        skill = registry.get(name)
        if skill is None:
            error_msg = f"Unknown tool: {name}"
            logger.warning(error_msg)
            return [TextContent(type="text", text=error_msg)]

        try:
            result = await skill.execute(MCP_SERVER_USER_ID, arguments)
            return [TextContent(type="text", text=result)]
        except Exception:
            logger.error("Error executing tool %s via MCP", name, exc_info=True)
            return [TextContent(type="text", text=f"Error executing tool: {name}")]

    return server


def create_sse_app(server: Server) -> Starlette:  # type: ignore[name-defined]  # noqa: F821
    """Create a Starlette app with SSE transport for the MCP server.

    Mount this on your FastAPI app to expose MCP over SSE:
        app.mount("/mcp", create_sse_app(server))
    """
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=sse_transport.handle_post_message, methods=["POST"]),
        ],
    )
