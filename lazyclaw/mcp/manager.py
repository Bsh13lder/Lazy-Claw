from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt, derive_server_key, encrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Active MCP client connections keyed by server_id
_active_clients: dict = {}


async def add_server(
    config: Config,
    user_id: str,
    name: str,
    transport: str,
    server_config: dict,
) -> str:
    """Add an MCP server connection. Returns the server ID."""
    key = derive_server_key(config.server_secret, user_id)
    server_id = str(uuid4())
    encrypted_config = encrypt(json.dumps(server_config), key)
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO mcp_connections (id, user_id, name, transport, config) "
            "VALUES (?, ?, ?, ?, ?)",
            (server_id, user_id, name, transport, encrypted_config),
        )
        await db.commit()
    logger.info("Added MCP server %s (%s) for user %s", name, transport, user_id)
    return server_id


async def remove_server(config: Config, user_id: str, server_id: str) -> bool:
    """Remove an MCP server connection. Disconnects if active. Returns True if deleted."""
    await disconnect_server(user_id, server_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM mcp_connections WHERE id = ? AND user_id = ?",
            (server_id, user_id),
        )
        await db.commit()
        deleted = cursor.rowcount > 0
    if deleted:
        logger.info("Removed MCP server %s for user %s", server_id, user_id)
    return deleted


async def list_servers(config: Config, user_id: str) -> list[dict]:
    """List all MCP server connections for a user with decrypted configs."""
    key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, transport, config, enabled, created_at "
            "FROM mcp_connections WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        results = await rows.fetchall()

    servers = []
    for row in results:
        raw_config = row[3]
        decrypted = decrypt(raw_config, key) if raw_config.startswith("enc:") else raw_config
        servers.append({
            "id": row[0],
            "name": row[1],
            "transport": row[2],
            "config": json.loads(decrypted),
            "enabled": bool(row[4]),
            "created_at": row[5],
            "connected": row[0] in _active_clients,
        })
    return servers


async def get_server(config: Config, user_id: str, server_id: str) -> dict | None:
    """Get a single MCP server connection with decrypted config."""
    key = derive_server_key(config.server_secret, user_id)
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id, name, transport, config, enabled, created_at "
            "FROM mcp_connections WHERE id = ? AND user_id = ?",
            (server_id, user_id),
        )
        result = await row.fetchone()

    if not result:
        return None

    raw_config = result[3]
    decrypted = decrypt(raw_config, key) if raw_config.startswith("enc:") else raw_config
    return {
        "id": result[0],
        "name": result[1],
        "transport": result[2],
        "config": json.loads(decrypted),
        "enabled": bool(result[4]),
        "created_at": result[5],
        "connected": result[0] in _active_clients,
    }


async def connect_server(config: Config, user_id: str, server_id: str) -> MCPClient:  # type: ignore[name-defined]  # noqa: F821
    """Connect to an MCP server. Returns the active MCPClient."""
    from lazyclaw.mcp.client import MCPClient

    server = await get_server(config, user_id, server_id)
    if not server:
        raise ValueError(f"MCP server {server_id} not found for user {user_id}")

    # Disconnect existing connection if any
    if server_id in _active_clients:
        await disconnect_server(user_id, server_id)

    client = MCPClient(
        server_id=server_id,
        name=server["name"],
        transport=server["transport"],
        config=server["config"],
    )
    await client.connect()
    _active_clients[server_id] = client
    logger.info("Connected to MCP server %s (%s)", server["name"], server_id)
    return client


async def disconnect_server(user_id: str, server_id: str) -> None:
    """Disconnect an active MCP client."""
    client = _active_clients.pop(server_id, None)
    if client is not None:
        try:
            await client.disconnect()
            logger.info("Disconnected MCP server %s", server_id)
        except Exception:
            logger.warning("Error disconnecting MCP server %s", server_id, exc_info=True)


async def reconnect_server(config: Config, user_id: str, server_id: str) -> MCPClient:  # type: ignore[name-defined]  # noqa: F821
    """Disconnect and reconnect to an MCP server."""
    await disconnect_server(user_id, server_id)
    return await connect_server(config, user_id, server_id)


def get_active_client(server_id: str) -> MCPClient | None:  # type: ignore[name-defined]  # noqa: F821
    """Get an active MCP client by server ID. Returns None if not connected."""
    return _active_clients.get(server_id)


async def disconnect_all() -> None:
    """Disconnect all active MCP clients."""
    server_ids = list(_active_clients.keys())
    for server_id in server_ids:
        client = _active_clients.pop(server_id, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.warning("Error disconnecting MCP server %s", server_id, exc_info=True)
    logger.info("Disconnected all MCP clients (%d total)", len(server_ids))
