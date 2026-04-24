"""MCP API — MCP server management."""

from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

_VALID_TRANSPORTS = {"stdio", "sse", "streamable_http"}


class AddServerRequest(BaseModel):
    name: str
    transport: str
    config: Dict

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        if v not in _VALID_TRANSPORTS:
            raise ValueError(
                f"Invalid transport '{v}'. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
            )
        return v


@router.get("/servers")
async def list_servers(user: User = Depends(get_current_user)):
    """List user's MCP server connections."""
    from lazyclaw.mcp.manager import list_servers

    servers = await list_servers(_config, user.id)
    return {"servers": servers}


@router.post("/servers")
async def add_server(body: AddServerRequest, user: User = Depends(get_current_user)):
    """Add a new MCP server connection."""
    from lazyclaw.mcp.manager import add_server

    server_id = await add_server(
        _config, user.id, body.name, body.transport, body.config
    )
    return {"id": server_id, "status": "ok"}


@router.get("/servers/{server_id}")
async def get_server(server_id: str, user: User = Depends(get_current_user)):
    """Get MCP server details."""
    from lazyclaw.mcp.manager import get_server

    server = await get_server(_config, user.id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.delete("/servers/{server_id}")
async def remove_server(server_id: str, user: User = Depends(get_current_user)):
    """Remove an MCP server connection."""
    from lazyclaw.mcp.manager import remove_server

    removed = await remove_server(_config, user.id, server_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"status": "deleted"}


class FavoriteRequest(BaseModel):
    favorite: bool


@router.post("/servers/{server_id}/favorite")
async def set_server_favorite(
    server_id: str, body: FavoriteRequest,
    user: User = Depends(get_current_user),
):
    """Mark / unmark an MCP server as favorite.

    Favorites auto-connect at boot and are exempt from idle-disconnect —
    see `lazyclaw/mcp/manager.py:set_favorite`.
    """
    from lazyclaw.mcp.manager import get_server, set_favorite

    server = await get_server(_config, user.id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    updated = await set_favorite(_config, user.id, server["name"], body.favorite)
    if not updated:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"status": "ok", "favorite": body.favorite}


@router.post("/servers/{server_id}/connect")
async def connect_server(server_id: str, user: User = Depends(get_current_user)):
    """Connect to an MCP server."""
    from lazyclaw.mcp.manager import connect_server

    try:
        await connect_server(_config, user.id, server_id)
    except Exception as exc:
        logger.error("MCP connect failed for server %s: %s", server_id, exc, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Failed to connect to MCP server. Check server configuration.",
        ) from exc
    return {"status": "connected"}


@router.post("/servers/{server_id}/disconnect")
async def disconnect_server(server_id: str, user: User = Depends(get_current_user)):
    """Disconnect from an MCP server."""
    from lazyclaw.mcp.manager import disconnect_server

    await disconnect_server(user.id, server_id)
    return {"status": "disconnected"}


@router.post("/servers/{server_id}/reconnect")
async def reconnect_server(server_id: str, user: User = Depends(get_current_user)):
    """Reconnect to an MCP server."""
    from lazyclaw.mcp.manager import reconnect_server

    await reconnect_server(_config, user.id, server_id)
    return {"status": "reconnected"}


@router.get("/servers/{server_id}/tools")
async def get_server_tools(server_id: str, user: User = Depends(get_current_user)):
    """Return actual tool names for an MCP server from the tool cache."""
    import json
    from lazyclaw.db.connection import db_session

    async with db_session(_config) as db:
        # Get server name from server_id
        row = await db.execute(
            "SELECT name FROM mcp_connections WHERE id = ? AND user_id = ?",
            (server_id, user.id),
        )
        result = await row.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Server not found")

        server_name = result[0]

        # Get cached tools
        row = await db.execute(
            "SELECT tools_json FROM mcp_tool_cache WHERE server_name = ?",
            (server_name,),
        )
        cache = await row.fetchone()
        if not cache:
            return {"tools": []}

        try:
            tools = json.loads(cache[0])
        except (json.JSONDecodeError, TypeError):
            return {"tools": []}

    return {
        "tools": [
            {"name": t.get("name", ""), "description": t.get("description", "")}
            for t in tools
            if isinstance(t, dict)
        ]
    }
