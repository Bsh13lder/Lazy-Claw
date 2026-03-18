"""MCP management skills — list, add, remove, connect, disconnect MCP servers.

Provides agent-accessible tools for managing MCP server connections
through the skill registry. All operations delegate to lazyclaw.mcp.manager.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


def _find_server_by_name(servers: list[dict], name: str) -> dict | None:
    """Find a server by name using case-insensitive contains matching."""
    name_lower = name.lower()
    # Exact match first
    for server in servers:
        if server["name"].lower() == name_lower:
            return server
    # Fuzzy contains match
    for server in servers:
        if name_lower in server["name"].lower():
            return server
    return None


def _format_server_table(servers: list[dict]) -> str:
    """Format a list of servers as a readable table."""
    if not servers:
        return "No MCP servers configured."

    lines = [f"{'Name':<24} {'Transport':<18} {'Connected':<11} {'Enabled':<9} Description"]
    lines.append("-" * 90)
    for s in servers:
        connected = "yes" if s.get("connected") else "no"
        enabled = "yes" if s.get("enabled") else "no"
        desc = (s.get("config", {}).get("description", "") or "")[:30]
        lines.append(
            f"{s['name']:<24} {s['transport']:<18} {connected:<11} {enabled:<9} {desc}"
        )
    return "\n".join(lines)


class ListMCPServersSkill(BaseSkill):
    """List all configured MCP server connections."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "mcp_management"

    @property
    def name(self) -> str:
        return "list_mcp_servers"

    @property
    def description(self) -> str:
        return (
            "List all configured MCP server connections with their status "
            "and transport type."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.mcp.manager import list_servers

            servers = await list_servers(self._config, user_id)
            return _format_server_table(servers)
        except Exception as exc:
            return f"Error listing MCP servers: {exc}"


class AddMCPServerSkill(BaseSkill):
    """Add a new MCP server connection."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "mcp_management"

    @property
    def name(self) -> str:
        return "add_mcp_server"

    @property
    def description(self) -> str:
        return (
            "Add a new MCP server connection. Supports stdio (command), "
            "SSE (url), or streamable HTTP (url) transport."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the MCP server connection",
                },
                "transport": {
                    "type": "string",
                    "enum": ["stdio", "sse", "streamable_http"],
                    "description": "Transport protocol to use",
                },
                "command": {
                    "type": "string",
                    "description": "Command for stdio transport, e.g. 'python -m my_server'",
                },
                "url": {
                    "type": "string",
                    "description": "URL for SSE or streamable_http transport",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of the server",
                },
            },
            "required": ["name", "transport"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.mcp.manager import add_server

            transport = params["transport"]
            server_name = params["name"]

            # Build server_config based on transport type
            server_config: dict = {}
            if transport == "stdio":
                command = params.get("command")
                if not command:
                    return "Error: 'command' is required for stdio transport"
                parts = command.split()
                server_config["command"] = parts[0]
                server_config["args"] = parts[1:] if len(parts) > 1 else []
            elif transport in ("sse", "streamable_http"):
                url = params.get("url")
                if not url:
                    return f"Error: 'url' is required for {transport} transport"
                server_config["url"] = url
            else:
                return f"Error: Unknown transport '{transport}'"

            if params.get("description"):
                server_config["description"] = params["description"]

            server_id = await add_server(
                self._config, user_id, server_name, transport, server_config
            )
            return (
                f"Added MCP server '{server_name}' ({transport}).\n"
                f"Server ID: {server_id}\n"
                f"Use connect_mcp_server to establish the connection."
            )
        except Exception as exc:
            return f"Error adding MCP server: {exc}"


class RemoveMCPServerSkill(BaseSkill):
    """Remove an MCP server connection by name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "mcp_management"

    @property
    def name(self) -> str:
        return "remove_mcp_server"

    @property
    def description(self) -> str:
        return "Remove an MCP server connection by name."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to remove",
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.mcp.manager import list_servers, remove_server

            servers = await list_servers(self._config, user_id)
            server = _find_server_by_name(servers, params["name"])
            if not server:
                available = ", ".join(s["name"] for s in servers) or "none"
                return (
                    f"Error: No MCP server matching '{params['name']}' found. "
                    f"Available: {available}"
                )

            deleted = await remove_server(self._config, user_id, server["id"])
            if deleted:
                return f"Removed MCP server '{server['name']}'."
            return f"Error: Failed to remove server '{server['name']}'."
        except Exception as exc:
            return f"Error removing MCP server: {exc}"


class ConnectMCPServerSkill(BaseSkill):
    """Connect or reconnect to an MCP server by name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "mcp_management"

    @property
    def name(self) -> str:
        return "connect_mcp_server"

    @property
    def description(self) -> str:
        return "Connect or reconnect to an MCP server by name."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to connect to",
                },
                "reconnect": {
                    "type": "boolean",
                    "description": "Force reconnect if already connected (default false)",
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.mcp.manager import (
                connect_server,
                list_servers,
                reconnect_server,
            )

            servers = await list_servers(self._config, user_id)
            server = _find_server_by_name(servers, params["name"])
            if not server:
                available = ", ".join(s["name"] for s in servers) or "none"
                return (
                    f"Error: No MCP server matching '{params['name']}' found. "
                    f"Available: {available}"
                )

            reconnect = params.get("reconnect", False)
            if reconnect:
                client = await reconnect_server(
                    self._config, user_id, server["id"]
                )
                return f"Reconnected to MCP server '{server['name']}'."

            client = await connect_server(self._config, user_id, server["id"])
            return f"Connected to MCP server '{server['name']}'."
        except Exception as exc:
            return f"Error connecting to MCP server: {exc}"


class DisconnectMCPServerSkill(BaseSkill):
    """Disconnect from an active MCP server."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "mcp_management"

    @property
    def name(self) -> str:
        return "disconnect_mcp_server"

    @property
    def description(self) -> str:
        return "Disconnect from an active MCP server."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the MCP server to disconnect",
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.mcp.manager import disconnect_server, list_servers

            servers = await list_servers(self._config, user_id)
            server = _find_server_by_name(servers, params["name"])
            if not server:
                available = ", ".join(s["name"] for s in servers) or "none"
                return (
                    f"Error: No MCP server matching '{params['name']}' found. "
                    f"Available: {available}"
                )

            if not server.get("connected"):
                return f"MCP server '{server['name']}' is not currently connected."

            await disconnect_server(user_id, server["id"])
            return f"Disconnected from MCP server '{server['name']}'."
        except Exception as exc:
            return f"Error disconnecting MCP server: {exc}"
