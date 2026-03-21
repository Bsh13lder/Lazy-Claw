from __future__ import annotations

import json
import logging
import sys
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt, derive_server_key, encrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Active MCP client connections keyed by server_id
_active_clients: dict = {}

# Bundled MCP servers that ship with LazyClaw
# "module" = Python module (run via sys.executable -m <module>)
# "npx" = npm package (run via npx <package>)
BUNDLED_MCPS = {
    "mcp-freeride": {
        "module": "mcp_freeride",
        "description": "Free AI router (ECO mode)",
    },
    "mcp-healthcheck": {
        "module": "mcp_healthcheck",
        "description": "AI provider health monitor",
    },
    "mcp-apihunter": {
        "module": "mcp_apihunter",
        "description": "Free API endpoint discovery",
    },
    "mcp-vaultwhisper": {
        "module": "mcp_vaultwhisper",
        "description": "Privacy-safe AI proxy (PII scrubbing)",
    },
    "mcp-taskai": {
        "module": "mcp_taskai",
        "description": "Task intelligence (categorize, prioritize, deduplicate)",
    },
    "mcp-lazydoctor": {
        "module": "mcp_lazydoctor",
        "description": "Self-healing doctor (lint, test, diagnose, auto-fix)",
    },
    "claude-code": {
        "npx": "@steipete/claude-code-mcp",
        "description": "Control Claude Code CLI from LazyClaw",
        # Strip ANTHROPIC_API_KEY so claude CLI uses Max subscription (OAuth),
        # not the API key which may have no credits
        "strip_env": ["ANTHROPIC_API_KEY"],
    },
}


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


async def connect_server_with_oauth(
    config: Config,
    user_id: str,
    server_id: str,
) -> MCPClient:  # type: ignore[name-defined]  # noqa: F821
    """Connect to an MCP server, handling OAuth 2.1 if needed.

    1. Check for cached valid tokens → inject Bearer header
    2. Try normal connect
    3. On 401 → run OAuth browser flow → reconnect with token
    """
    from lazyclaw.mcp.token_store import is_token_expired, load_tokens

    server = await get_server(config, user_id, server_id)
    if not server:
        raise ValueError(f"MCP server {server_id} not found for user {user_id}")

    # Check for cached valid tokens
    tokens = await load_tokens(config, user_id, server["name"])
    if tokens and not is_token_expired(tokens):
        return await connect_with_bearer(
            config, user_id, server_id, server, tokens.access_token,
        )

    # Try connecting without auth first
    try:
        return await connect_server(config, user_id, server_id)
    except Exception as exc:
        # Check if it's a 401 requiring OAuth
        metadata_url = _extract_resource_metadata(exc)
        if not metadata_url:
            raise  # Not a 401 or no metadata — let it propagate

    # 401 detected — run OAuth flow via browser
    logger.info("MCP server %s requires OAuth — opening browser", server["name"])

    from lazyclaw.mcp.oauth import run_oauth_flow

    tokens = await run_oauth_flow(
        config=config,
        user_id=user_id,
        server_name=server["name"],
        server_url=server["config"].get("url", ""),
        resource_metadata_url=metadata_url,
    )
    return await connect_with_bearer(
        config, user_id, server_id, server, tokens.access_token,
    )


async def connect_with_bearer(
    config: Config,
    user_id: str,
    server_id: str,
    server: dict,
    access_token: str,
) -> MCPClient:  # type: ignore[name-defined]  # noqa: F821
    """Inject Bearer header into server config and (re)connect."""
    # Disconnect existing connection
    await disconnect_server(user_id, server_id)

    # Build new config with auth headers (immutable — new dict)
    updated_config = {
        **server["config"],
        "headers": {"Authorization": f"Bearer {access_token}"},
    }

    # Persist updated config to DB so reconnects preserve auth
    key = derive_server_key(config.server_secret, user_id)
    encrypted_config = encrypt(json.dumps(updated_config), key)
    async with db_session(config) as db:
        await db.execute(
            "UPDATE mcp_connections SET config = ? WHERE id = ? AND user_id = ?",
            (encrypted_config, server_id, user_id),
        )
        await db.commit()

    return await connect_server(config, user_id, server_id)


def _extract_resource_metadata(exc: BaseException) -> str | None:
    """Try to extract resource_metadata URL from an OAuth 401 error.

    Checks both the exception itself and its __cause__ for
    httpx.HTTPStatusError with a 401 response.
    """
    from lazyclaw.mcp.oauth import parse_resource_metadata_url

    for candidate in (exc, getattr(exc, "__cause__", None)):
        if candidate is None:
            continue
        response = getattr(candidate, "response", None)
        if getattr(response, "status_code", None) != 401:
            continue
        header = response.headers.get("www-authenticate", "")
        url = parse_resource_metadata_url(header)
        if url:
            return url
    return None


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


async def auto_register_bundled_mcps(
    config: Config,
    user_id: str,
) -> list[str]:
    """Auto-register bundled MCP servers that are installed but not yet in DB.

    Checks each bundled MCP module — if importable and not already registered
    for this user, adds a stdio connection entry to the database.

    Returns list of newly registered server names.
    """
    key = derive_server_key(config.server_secret, user_id)
    registered: list[str] = []

    # Get existing server names for this user
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT name FROM mcp_connections WHERE user_id = ?",
            (user_id,),
        )
        existing_names = {row[0] for row in await rows.fetchall()}

    for name, info in BUNDLED_MCPS.items():
        # Skip if already registered
        if name in existing_names:
            continue

        # Determine if this is a Python module or npx package
        if "module" in info:
            # Python module — check if importable
            try:
                __import__(info["module"])
            except ImportError:
                continue
            server_config = {
                "command": sys.executable,
                "args": ["-m", info["module"]],
            }
        elif "npx" in info:
            # npm package — check if npx is available
            import shutil
            if not shutil.which("npx"):
                continue
            # Build env — strip keys that should use OAuth instead of API
            import os as _os
            npx_env = dict(info.get("env", {}))
            for strip_key in info.get("strip_env", []):
                npx_env.setdefault(strip_key, "")  # Empty = unset for subprocess
            server_config = {
                "command": "npx",
                "args": [info["npx"]],
                "env": npx_env,
            }
        else:
            continue
        server_id = str(uuid4())
        encrypted_config = encrypt(json.dumps(server_config), key)

        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO mcp_connections (id, user_id, name, transport, config) "
                "VALUES (?, ?, ?, ?, ?)",
                (server_id, user_id, name, "stdio", encrypted_config),
            )
            await db.commit()

        logger.info("Auto-registered bundled MCP: %s (id=%s)", name, server_id)
        registered.append(name)

    if registered:
        logger.info("Auto-registered %d bundled MCP servers", len(registered))
    return registered


async def connect_and_register_bundled_mcps(
    config: Config,
    user_id: str,
    registry,
) -> int:
    """Auto-register, connect, and register tools from all bundled MCPs.

    This is the all-in-one startup function: ensures bundled MCPs are in DB,
    connects to each enabled one, and registers their tools in the skill registry.

    Returns total number of tools registered.
    """
    from lazyclaw.mcp.bridge import register_mcp_tools

    # Step 1: ensure DB entries exist
    await auto_register_bundled_mcps(config, user_id)

    # Step 2: connect + register tools for all enabled bundled MCPs
    total_tools = 0
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name FROM mcp_connections "
            "WHERE user_id = ? AND enabled = 1 AND name IN ({})".format(
                ",".join("?" for _ in BUNDLED_MCPS)
            ),
            (user_id, *BUNDLED_MCPS.keys()),
        )
        servers = await rows.fetchall()

    # Connect all MCP servers in parallel (was sequential — 12s → ~2s)
    async def _connect_one(server_id: str, name: str) -> int:
        if server_id in _active_clients:
            return 0
        try:
            client = await connect_server(config, user_id, server_id)
            count = await register_mcp_tools(
                client, registry, config=config, user_id=user_id,
            )
            logger.info("Connected bundled MCP %s: %d tools", name, count)
            return count
        except Exception:
            logger.warning("Failed to connect bundled MCP %s", name, exc_info=True)
            return 0

    import asyncio
    results = await asyncio.gather(
        *(_connect_one(sid, name) for sid, name in servers),
        return_exceptions=True,
    )
    total_tools = sum(r for r in results if isinstance(r, int))

    return total_tools
