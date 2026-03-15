from __future__ import annotations

import json
import logging
from dataclasses import asdict

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_apihunter.config import ApiHunterConfig
from mcp_apihunter.registry import Registry
from mcp_apihunter.validator import validate_entry

logger = logging.getLogger(__name__)


def _entry_to_text(entry) -> str:
    """Format a RegistryEntry as readable JSON."""
    data = asdict(entry)
    data["models"] = list(data["models"])
    return json.dumps(data, indent=2)


def create_server(registry: Registry, config: ApiHunterConfig) -> Server:
    """Create the MCP server with 5 API Hunter tools."""
    server = Server("mcp-apihunter")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="apihunter_submit",
                description="Submit a free API endpoint to the registry. Auto-validates if enabled.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Provider name (e.g. 'groq', 'my-proxy')"},
                        "base_url": {"type": "string", "description": "Base URL (e.g. 'https://api.groq.com/openai')"},
                        "models": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of model IDs available at this endpoint",
                        },
                        "api_key_env": {
                            "type": "string",
                            "description": "Environment variable name holding the API key (optional)",
                        },
                        "added_by": {"type": "string", "description": "Who submitted this (default: 'anonymous')"},
                    },
                    "required": ["name", "base_url", "models"],
                },
            ),
            Tool(
                name="apihunter_validate",
                description="Force re-validate an endpoint by its registry ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Registry entry ID"},
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="apihunter_list",
                description="List all endpoints in the registry, optionally filtered by status.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "Filter by status: pending, active, failed, removed",
                        },
                    },
                },
            ),
            Tool(
                name="apihunter_search",
                description="Search endpoints by name, model, or URL.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search term"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="apihunter_remove",
                description="Mark an endpoint as removed by its registry ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Registry entry ID"},
                    },
                    "required": ["id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "apihunter_submit":
            return await _handle_submit(arguments)
        elif name == "apihunter_validate":
            return await _handle_validate(arguments)
        elif name == "apihunter_list":
            return await _handle_list(arguments)
        elif name == "apihunter_search":
            return await _handle_search(arguments)
        elif name == "apihunter_remove":
            return await _handle_remove(arguments)
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    async def _handle_submit(arguments: dict) -> list[TextContent]:
        entry = await registry.add(
            name=arguments["name"],
            base_url=arguments["base_url"],
            api_key_env=arguments.get("api_key_env"),
            models=arguments["models"],
            added_by=arguments.get("added_by", "anonymous"),
        )

        if config.auto_validate:
            result = await validate_entry(entry, config.validation_timeout)
            new_status = "active" if result.success else "failed"
            entry = await registry.update_status(
                entry.id, new_status, result.latency_ms if result.success else None, result.timestamp
            )
            validation_msg = f"\nValidation: {'PASSED' if result.success else 'FAILED'}"
            if result.error:
                validation_msg += f" — {result.error}"
            if result.model_responded:
                validation_msg += f" (model: {result.model_responded})"
        else:
            validation_msg = "\nAuto-validation disabled. Use apihunter_validate to test."

        return [TextContent(type="text", text=_entry_to_text(entry) + validation_msg)]

    async def _handle_validate(arguments: dict) -> list[TextContent]:
        entry = await registry.get(arguments["id"])
        if entry is None:
            return [TextContent(type="text", text=f"Error: Entry {arguments['id']} not found")]

        result = await validate_entry(entry, config.validation_timeout)
        new_status = "active" if result.success else "failed"
        updated = await registry.update_status(
            entry.id, new_status, result.latency_ms if result.success else None, result.timestamp
        )

        msg = f"Validation {'PASSED' if result.success else 'FAILED'}"
        if result.error:
            msg += f" — {result.error}"
        if result.latency_ms > 0:
            msg += f" ({result.latency_ms:.0f}ms)"
        if result.model_responded:
            msg += f" [model: {result.model_responded}]"
        msg += "\n\n" + _entry_to_text(updated)

        return [TextContent(type="text", text=msg)]

    async def _handle_list(arguments: dict) -> list[TextContent]:
        status_filter = arguments.get("status")
        entries = await registry.list_all(status_filter)
        if not entries:
            return [TextContent(type="text", text="No endpoints found.")]

        lines = [f"{'ID':<5} {'Name':<20} {'Status':<10} {'Latency':>10} {'Models'}"]
        lines.append("-" * 80)
        for e in entries:
            latency = f"{e.latency_avg_ms:.0f}ms" if e.latency_avg_ms else "n/a"
            models_str = ", ".join(e.models[:3])
            if len(e.models) > 3:
                models_str += f" (+{len(e.models) - 3})"
            lines.append(f"{e.id:<5} {e.name:<20} {e.status:<10} {latency:>10} {models_str}")

        return [TextContent(type="text", text="\n".join(lines))]

    async def _handle_search(arguments: dict) -> list[TextContent]:
        entries = await registry.search(arguments["query"])
        if not entries:
            return [TextContent(type="text", text=f"No endpoints matching '{arguments['query']}'.")]

        results = [_entry_to_text(e) for e in entries]
        return [TextContent(type="text", text="\n---\n".join(results))]

    async def _handle_remove(arguments: dict) -> list[TextContent]:
        removed = await registry.remove(arguments["id"])
        if removed:
            return [TextContent(type="text", text=f"Entry {arguments['id']} marked as removed.")]
        return [TextContent(type="text", text=f"Error: Entry {arguments['id']} not found or already removed.")]

    return server
