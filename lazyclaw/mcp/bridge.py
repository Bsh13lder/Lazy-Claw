from __future__ import annotations

import logging
import time
from typing import Any

from lazyclaw.mcp.client import MCPClient
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MCP_PREFIX = "mcp_"


class MCPToolSkill(BaseSkill):
    """Wraps a single MCP tool as a LazyClaw BaseSkill.

    When config and user_id are provided, enables automatic token
    refresh on 401 errors from remote OAuth-protected MCP servers.
    """

    def __init__(
        self,
        client: MCPClient,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        config: Any = None,
        user_id: str | None = None,
    ) -> None:
        self._client = client
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._tool_schema = tool_schema
        self._config = config
        self._user_id = user_id

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
        """Execute the MCP tool via the client connection.

        On 401, attempts token refresh and retries once.
        """
        try:
            return await self._client.call_tool(self._tool_name, params)
        except Exception as exc:
            if not self._config or not _is_auth_error(exc):
                raise
            logger.info(
                "MCP tool %s got 401 — attempting token refresh",
                self._tool_name,
            )
            await self._refresh_and_reconnect(user_id)
            return await self._client.call_tool(self._tool_name, params)

    async def _refresh_and_reconnect(self, user_id: str) -> None:
        """Refresh OAuth token and reconnect the MCP client."""
        from lazyclaw.mcp.manager import _connect_with_bearer, get_server
        from lazyclaw.mcp.oauth import refresh_access_token
        from lazyclaw.mcp.token_store import OAuthTokenData, load_tokens, save_tokens

        server_name = self._client.name
        tokens = await load_tokens(self._config, user_id, server_name)
        if not tokens or not tokens.refresh_token:
            raise RuntimeError(
                f"No refresh token for {server_name}. "
                "Re-authenticate: 'reconnect to " + server_name + "'"
            )

        token_dict = await refresh_access_token(
            tokens.token_endpoint, tokens.refresh_token, tokens.client_id,
        )

        new_tokens = OAuthTokenData(
            access_token=token_dict["access_token"],
            refresh_token=token_dict.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + token_dict.get("expires_in", 3600),
            scope=token_dict.get("scope", tokens.scope),
            metadata_url=tokens.metadata_url,
            token_endpoint=tokens.token_endpoint,
            client_id=tokens.client_id,
        )
        await save_tokens(self._config, user_id, server_name, new_tokens)
        logger.info("OAuth token refreshed for %s", server_name)

        server = await get_server(self._config, user_id, self._client.server_id)
        if server:
            await _connect_with_bearer(
                self._config, user_id, self._client.server_id,
                server, new_tokens.access_token,
            )


def _is_auth_error(exc: BaseException) -> bool:
    """Check if an exception is a 401 HTTP error."""
    for candidate in (exc, getattr(exc, "__cause__", None)):
        if candidate is None:
            continue
        status = getattr(
            getattr(candidate, "response", None), "status_code", None,
        )
        if status == 401:
            return True
    return False


async def register_mcp_tools(
    client: MCPClient,
    registry: SkillRegistry,
    config: Any = None,
    user_id: str | None = None,
) -> int:
    """Discover tools from an MCP client and register each as a skill.

    When config and user_id are provided, registered skills gain
    automatic 401 token refresh capability.

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
            config=config,
            user_id=user_id,
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
    if to_remove:
        registry._invalidate_cache()
    logger.info(
        "Unregistered %d tools for MCP server %s", len(to_remove), server_id
    )
    return len(to_remove)
