from __future__ import annotations

import logging
from typing import Any

from lazyclaw.mcp.client import MCPClient
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MCP_PREFIX = "mcp_"


class MCPToolSkill(BaseSkill):
    """Wraps a single MCP tool as a LazyClaw BaseSkill."""

    def __init__(
        self,
        client: MCPClient,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> None:
        self._client = client
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._tool_schema = tool_schema

    @property
    def name(self) -> str:
        return f"{_MCP_PREFIX}{self._client.server_id}_{self._tool_name}"

    @property
    def display_name(self) -> str:
        return f"{self._client.name}:{self._tool_name}"

    @property
    def description(self) -> str:
        return f"[MCP: {self._client.name}] {self._tool_description}"

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._tool_schema

    async def execute(self, user_id: str, params: dict[str, Any]) -> str:
        """Execute the MCP tool via the client connection."""
        return await self._client.call_tool(self._tool_name, params)


async def register_mcp_tools(client: MCPClient, registry: SkillRegistry) -> int:
    """Discover tools from an MCP client and register each as a skill.

    Returns the number of tools registered.
    """
    tools = await client.list_tools()
    count = 0
    for tool in tools:
        skill = MCPToolSkill(
            client=client,
            tool_name=tool["name"],
            tool_description=tool.get("description", ""),
            tool_schema=tool.get("inputSchema", {}),
        )
        registry.register(skill)
        logger.info("Registered MCP tool: %s", skill.name)
        count += 1
    logger.info(
        "Registered %d tools from MCP server %s", count, client.server_id
    )
    return count


def unregister_mcp_tools(server_id: str, registry: SkillRegistry) -> int:
    """Remove all MCP skills for a given server_id from the registry.

    Returns the number of skills removed.
    """
    prefix = f"{_MCP_PREFIX}{server_id}_"
    # Collect matching names from the internal dict
    to_remove = [
        name for name in list(registry._skills) if name.startswith(prefix)
    ]
    for name in to_remove:
        del registry._skills[name]
        logger.info("Unregistered MCP tool: %s", name)
    logger.info(
        "Unregistered %d tools for MCP server %s", len(to_remove), server_id
    )
    return len(to_remove)
