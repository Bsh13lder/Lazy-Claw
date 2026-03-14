"""MCP API — MCP server management."""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

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


@router.post("/servers/{server_id}/connect")
async def connect_server(server_id: str, user: User = Depends(get_current_user)):
    """Connect to an MCP server."""
    from lazyclaw.mcp.manager import connect_server

    try:
        await connect_server(_config, user.id, server_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
