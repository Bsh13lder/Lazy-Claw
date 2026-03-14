"""Connector routes — REST API and WebSocket for remote computer connectors."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.computer.connector_server import ConnectorServer
from lazyclaw.gateway.auth import (
    User,
    authenticate_user,
    get_current_user,
)

logger = logging.getLogger(__name__)

_config = load_config()
_connector_server = ConnectorServer(_config)


def get_connector_server() -> ConnectorServer:
    """Get the shared ConnectorServer singleton (used by skills)."""
    return _connector_server


router = APIRouter(prefix="/api/connector", tags=["connector"])


class TokenRequest(BaseModel):
    username: str
    password: str


@router.post("/token")
async def create_connector_token(body: TokenRequest):
    """Generate a connector token (used by standalone connector setup)."""
    user = await authenticate_user(_config, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = await _connector_server.create_token(user.id)
    return {"token": token}


@router.get("/status")
async def connector_status(user: User = Depends(get_current_user)):
    """Check if the user's connector is online."""
    connected = _connector_server.is_connected(user.id)
    device_info = _connector_server.get_device_info(user.id)
    return {
        "connected": connected,
        "device_info": device_info,
    }


@router.delete("/token")
async def revoke_connector_token(user: User = Depends(get_current_user)):
    """Revoke the user's connector token."""
    deleted = await _connector_server.delete_token(user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No token found")
    return {"deleted": True}


# ==================== WebSocket Endpoint ====================
# This uses a separate router with no prefix so the WS path is /ws/connector
ws_router = APIRouter(tags=["connector-ws"])


@ws_router.websocket("/ws/connector")
async def connector_websocket(ws: WebSocket):
    """WebSocket endpoint for remote computer connectors."""
    # Auth via Bearer token in headers
    auth_header = ws.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        await ws.close(code=4001, reason="Missing authorization")
        return

    token = auth_header[7:]
    user_id = await _connector_server.validate_token(token)
    if not user_id:
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    logger.info(f"Connector WebSocket connected: user={user_id}")

    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from connector: {raw[:100]}")
                continue

            msg_type = msg.get("type", "")

            if msg_type == "register":
                _connector_server.register(
                    user_id,
                    ws,
                    {
                        "platform": msg.get("platform", "unknown"),
                        "hostname": msg.get("hostname", "unknown"),
                        "username": msg.get("username", "unknown"),
                        "python_version": msg.get("python_version", ""),
                    },
                )

            elif msg_type == "heartbeat":
                pass  # Keep-alive, nothing to do

            elif msg_type == "result":
                cmd_id = msg.get("id", "")
                if cmd_id:
                    _connector_server.report_result(cmd_id, {
                        "success": msg.get("success", False),
                        "data": msg.get("data"),
                        "error": msg.get("error"),
                    })

    except WebSocketDisconnect:
        logger.info(f"Connector disconnected: user={user_id}")
    except Exception as e:
        logger.error(f"Connector WebSocket error: {e}")
    finally:
        _connector_server.unregister(user_id)
