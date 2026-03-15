"""MCP server with 4 health-check tools."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_healthcheck.monitor import Monitor

logger = logging.getLogger(__name__)


def create_server(monitor: Monitor) -> Server:
    """Wire up the MCP tool handlers and return the server."""
    server = Server("mcp-healthcheck")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="healthcheck_status",
                description="Full health status of all monitored AI providers.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="healthcheck_leaderboard",
                description="Ranked leaderboard of AI providers scored by speed, uptime, and quality.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="healthcheck_ping",
                description="Force-ping a single AI provider and return the result.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Provider name (e.g. groq, gemini, mistral)",
                        },
                    },
                    "required": ["provider"],
                },
            ),
            Tool(
                name="healthcheck_history",
                description="Recent ping history for a single AI provider.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Provider name (e.g. groq, gemini, mistral)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of recent checks to return (default 20)",
                        },
                    },
                    "required": ["provider"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "healthcheck_status":
            return _handle_status(monitor)

        if name == "healthcheck_leaderboard":
            return _handle_leaderboard(monitor)

        if name == "healthcheck_ping":
            return await _handle_ping(monitor, arguments)

        if name == "healthcheck_history":
            return _handle_history(monitor, arguments)

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _handle_status(monitor: Monitor) -> list[TextContent]:
    status = monitor.get_status()
    return [TextContent(type="text", text=json.dumps(status, indent=2))]


def _handle_leaderboard(monitor: Monitor) -> list[TextContent]:
    board = monitor.get_leaderboard()
    if not board:
        return [TextContent(type="text", text="No providers monitored yet.")]

    lines = [
        f"{'#':<4} {'Provider':<15} {'Score':>7} {'Speed':>7} {'Uptime':>7} {'Quality':>7} {'Avg ms':>8} {'Checks':>7}",
        "-" * 68,
    ]
    for rank, sp in enumerate(board, 1):
        lines.append(
            f"{rank:<4} {sp.name:<15} {sp.score:>7.3f} {sp.speed_score:>7.3f} "
            f"{sp.uptime_score:>7.3f} {sp.quality_score:>7.3f} "
            f"{sp.summary.avg_latency_ms:>7.0f}ms {sp.summary.total_checks:>7}"
        )
    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_ping(monitor: Monitor, arguments: dict) -> list[TextContent]:
    provider = arguments.get("provider", "")
    if provider not in monitor.endpoint_names:
        available = ", ".join(monitor.endpoint_names) or "(none configured)"
        return [TextContent(
            type="text",
            text=f"Unknown provider: {provider}. Available: {available}",
        )]
    result = await monitor.ping_one(provider)
    if result is None:
        return [TextContent(type="text", text=f"Provider not found: {provider}")]
    return [TextContent(type="text", text=json.dumps(asdict(result), indent=2))]


def _handle_history(monitor: Monitor, arguments: dict) -> list[TextContent]:
    provider = arguments.get("provider", "")
    limit = arguments.get("limit", 20)
    if provider not in monitor.endpoint_names:
        available = ", ".join(monitor.endpoint_names) or "(none configured)"
        return [TextContent(
            type="text",
            text=f"Unknown provider: {provider}. Available: {available}",
        )]
    history = monitor.get_provider_history(provider, limit)
    if not history:
        return [TextContent(type="text", text=f"No history for {provider} yet.")]
    rows = [asdict(r) for r in history]
    return [TextContent(type="text", text=json.dumps(rows, indent=2))]
