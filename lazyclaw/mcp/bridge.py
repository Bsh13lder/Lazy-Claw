from __future__ import annotations

import json
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
            new_client = await self._refresh_and_reconnect(user_id)
            # Update to the new active client (old one is disconnected)
            self._client = new_client
            return await self._client.call_tool(self._tool_name, params)

    async def _refresh_and_reconnect(self, user_id: str) -> MCPClient:
        """Refresh OAuth token and reconnect the MCP client.

        Returns the new active MCPClient (old one is disconnected).
        """
        from lazyclaw.mcp.manager import connect_with_bearer, get_server
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
        if not server:
            raise RuntimeError(f"Server {self._client.server_id} not found after refresh")
        return await connect_with_bearer(
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


class LazyMCPToolSkill(BaseSkill):
    """MCP tool stub that connects the server on first invocation.

    Registered from cached tool schemas — no subprocess spawned until
    the agent actually calls this tool. After connecting, subsequent
    calls go through the live client. If the server is idle-disconnected,
    the next call reconnects automatically.
    """

    def __init__(
        self,
        server_id: str,
        server_name: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        config: Any,
        user_id: str,
        is_oauth: bool = False,
    ) -> None:
        self._server_id = server_id
        self._server_name = server_name
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._tool_schema = tool_schema
        self._config = config
        self._user_id = user_id
        self._is_oauth = is_oauth

    @property
    def name(self) -> str:
        return f"{_MCP_PREFIX}{self._server_id}_{self._tool_name}"

    @property
    def display_name(self) -> str:
        return f"{self._server_name}:{self._tool_name}"

    @property
    def description(self) -> str:
        return f"[MCP: {self._server_name}] {self._tool_description}"

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._tool_schema

    async def execute(self, user_id: str, params: dict[str, Any]) -> str:
        """Connect on demand, call the tool, reset idle timer."""
        from lazyclaw.mcp.manager import (
            _active_clients,
            _get_connect_lock,
            connect_server,
            connect_server_with_oauth,
            touch_client,
        )

        # Serialize connects per server to prevent duplicate subprocesses
        async with _get_connect_lock(self._server_id):
            client = _active_clients.get(self._server_id)
            if client is None:
                logger.info(
                    "Lazy-connecting MCP server %s for tool %s",
                    self._server_name, self._tool_name,
                )
                if self._is_oauth:
                    client = await connect_server_with_oauth(
                        self._config, self._user_id, self._server_id,
                    )
                else:
                    client = await connect_server(
                        self._config, self._user_id, self._server_id,
                    )

        touch_client(self._server_id)
        return await client.call_tool(self._tool_name, params)


async def register_mcp_tools_lazy(
    server_id: str,
    server_name: str,
    tools_json: str,
    registry: SkillRegistry,
    config: Any,
    user_id: str,
    is_oauth: bool = False,
) -> int:
    """Register MCP tools as lazy stubs from cached schemas.

    No subprocess is spawned. Each tool connects the server on first call.
    Returns the number of tools registered.
    """
    tools = json.loads(tools_json)
    count = 0
    for tool in tools:
        base_name = tool["name"]
        # Skip if a built-in skill with the same name exists
        if registry.get(base_name) is not None:
            logger.info("Skipping lazy MCP tool %s_%s — built-in skill '%s' exists",
                        server_id, base_name, base_name)
            continue
        # Skip if another MCP server already provides this tool
        existing_mcp = registry.get_mcp_by_base_name(base_name)
        if existing_mcp is not None:
            logger.info("Skipping lazy MCP tool %s_%s — already provided by %s",
                        server_id, base_name, existing_mcp.name)
            continue
        skill = LazyMCPToolSkill(
            server_id=server_id,
            server_name=server_name,
            tool_name=tool["name"],
            tool_description=tool.get("description", ""),
            tool_schema=tool.get("inputSchema", {}),
            config=config,
            user_id=user_id,
            is_oauth=is_oauth,
        )
        registry.register(skill)
        count += 1
    logger.info(
        "Registered %d lazy tool stubs for MCP server %s", count, server_name,
    )
    return count


# -- Tool schema cache -------------------------------------------------------


async def cache_tool_schemas(config: Any, server_name: str, tools: list[dict]) -> None:
    """Save tool schemas to the mcp_tool_cache table."""
    from lazyclaw.db.connection import db_session

    tools_json = json.dumps(tools)
    async with db_session(config) as db:
        await db.execute(
            "INSERT OR REPLACE INTO mcp_tool_cache (server_name, tools_json, cached_at) "
            "VALUES (?, ?, datetime('now'))",
            (server_name, tools_json),
        )
        await db.commit()
    logger.debug("Cached %d tool schemas for %s", len(tools), server_name)


async def load_cached_schemas(config: Any, server_name: str) -> str | None:
    """Load cached tool schemas JSON. Returns None if not cached."""
    from lazyclaw.db.connection import db_session

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT tools_json FROM mcp_tool_cache WHERE server_name = ?",
            (server_name,),
        )
        result = await row.fetchone()
    return result[0] if result else None


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
        base_name = tool["name"]
        # Skip if a built-in skill with the same name exists
        if registry.get(base_name) is not None:
            logger.info("Skipping MCP tool %s_%s — built-in skill '%s' exists",
                        client.server_id, base_name, base_name)
            continue
        # Skip if another MCP server already provides this tool
        existing_mcp = registry.get_mcp_by_base_name(base_name)
        if existing_mcp is not None:
            logger.info("Skipping MCP tool %s_%s — already provided by %s",
                        client.server_id, base_name, existing_mcp.name)
            continue
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
