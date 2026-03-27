from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt, derive_server_key, encrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Active MCP client connections keyed by server_id
_active_clients: dict[str, Any] = {}

# Idle timeout — auto-disconnect after this many seconds of no tool calls.
# Favorites are exempt from idle disconnect.
MCP_IDLE_TIMEOUT_SECONDS = 300

_idle_timers: dict[str, asyncio.TimerHandle] = {}
_favorite_server_ids: set[str] = set()
_connect_locks: dict[str, asyncio.Lock] = {}
# Version counter per server — incremented on every tool call,
# checked by idle timer to avoid disconnecting during active use.
_activity_versions: dict[str, int] = {}

# Reference to skill registry for idle-disconnect cleanup.
# Set by connect_and_register_bundled_mcps() at startup.
_registry_ref: Any = None


def _get_connect_lock(server_id: str) -> asyncio.Lock:
    """Get or create a per-server lock for connect serialization."""
    lock = _connect_locks.get(server_id)
    if lock is None:
        lock = asyncio.Lock()
        _connect_locks[server_id] = lock
    return lock

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
        "description": "Full coding agent — build features, debug, refactor, code review, run tests. Use for complex code tasks. For simple file edits use write_file instead.",
        # Strip ANTHROPIC_API_KEY so claude CLI uses Max subscription (OAuth),
        # not the API key which may have no credits
        "strip_env": ["ANTHROPIC_API_KEY"],
    },
    "mcp-jobspy": {
        "module": "mcp_jobspy",
        "description": "Job search across Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google",
        "optional": True,
    },
    "stripe": {
        "npx": "@stripe/mcp@latest",
        "description": "Create invoices, track payments, manage subscriptions",
        "optional": True,
        "env_required": ["STRIPE_SECRET_KEY"],
    },
    "canva": {
        "remote": "https://mcp.canva.com/mcp",
        "description": "Design in Canva — create, edit, export presentations, social posts, logos",
        "optional": True,
        "oauth": True,
    },
    "mcp-instagram": {
        "module": "mcp_instagram",
        "description": "Instagram DMs, feed, posting — no browser (private mobile API)",
        "optional": True,
    },
    "mcp-whatsapp": {
        "node": "mcp-whatsapp/src/index.js",
        "description": "WhatsApp messaging — no browser (QR auth, web protocol)",
        "optional": True,
    },
    "mcp-email": {
        "module": "mcp_email",
        "description": "Send/read/search email via SMTP+IMAP — Gmail, Outlook, any provider",
        "optional": True,
    },
}


# -- Idle timeout management --------------------------------------------------


def touch_client(server_id: str) -> None:
    """Reset the idle timer for a server. Call after every tool invocation.

    Increments a version counter so the idle timer callback can detect
    whether new activity occurred since the timer was scheduled.
    Favorites are exempt — they never get idle-disconnected.
    """
    # Increment version — signals that this server is actively being used
    _activity_versions[server_id] = _activity_versions.get(server_id, 0) + 1

    timer = _idle_timers.pop(server_id, None)
    if timer is not None:
        timer.cancel()

    if server_id in _favorite_server_ids:
        return  # Favorites never idle-disconnect

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # No event loop — skip timer (e.g. during shutdown)

    # Capture current version — timer callback will compare against it
    version_at_schedule = _activity_versions[server_id]
    _idle_timers[server_id] = loop.call_later(
        MCP_IDLE_TIMEOUT_SECONDS,
        _schedule_idle_disconnect, server_id, version_at_schedule,
    )


def _schedule_idle_disconnect(server_id: str, version_at_schedule: int) -> None:
    """Sync callback for call_later — schedules the async disconnect."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_idle_disconnect(server_id, version_at_schedule))
    except RuntimeError:
        pass  # Event loop closed — nothing to do


async def _idle_disconnect(server_id: str, version_at_schedule: int) -> None:
    """Disconnect a server due to idle timeout and unregister its tools.

    Only disconnects if no tool calls have happened since the timer was
    scheduled (version_at_schedule matches current version).
    """
    _idle_timers.pop(server_id, None)

    # Version mismatch = tool call happened while timer was pending → skip
    current_version = _activity_versions.get(server_id, 0)
    if current_version != version_at_schedule:
        logger.debug(
            "Skipping idle disconnect for %s: version %d != %d (activity during timer)",
            server_id, version_at_schedule, current_version,
        )
        return

    client = _active_clients.get(server_id)
    if client is None:
        return  # Already disconnected

    name = client.name
    logger.info("Idle-disconnecting MCP server %s (%s)", name, server_id)

    client = _active_clients.pop(server_id, None)
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            logger.warning("Error idle-disconnecting %s", name, exc_info=True)

    # Note: lazy stubs remain in registry — next call will reconnect.
    # We don't unregister tools because LazyMCPToolSkill checks
    # _active_clients on every call and reconnects if needed.


# -- Favorite management -----------------------------------------------------


async def set_favorite(
    config: Config, user_id: str, server_name: str, favorite: bool,
) -> bool:
    """Set or unset a server as favorite. Returns True if updated."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "UPDATE mcp_connections SET favorite = ? "
            "WHERE user_id = ? AND name = ?",
            (1 if favorite else 0, user_id, server_name),
        )
        updated = cursor.rowcount > 0

        if updated:
            # Update in-memory favorite set in same session
            row = await db.execute(
                "SELECT id FROM mcp_connections WHERE user_id = ? AND name = ?",
                (user_id, server_name),
            )
            result = await row.fetchone()
            if result:
                if favorite:
                    _favorite_server_ids.add(result[0])
                else:
                    _favorite_server_ids.discard(result[0])

        await db.commit()

    if updated:
        logger.info(
            "Set favorite=%s for MCP server %s", favorite, server_name,
        )
    return updated


async def get_favorite_server_ids(config: Config, user_id: str) -> set[str]:
    """Get server IDs of all favorite MCP servers for a user."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id FROM mcp_connections "
            "WHERE user_id = ? AND favorite = 1",
            (user_id,),
        )
        return {row[0] for row in await rows.fetchall()}


async def get_server_id_by_name(
    config: Config, user_id: str, name: str,
) -> str | None:
    """Look up the server ID for a named MCP connection. Returns None if not found."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id FROM mcp_connections WHERE user_id = ? AND name = ?",
            (user_id, name),
        )
        result = await row.fetchone()
    return result[0] if result else None


# -- CRUD operations ----------------------------------------------------------


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
            "SELECT id, name, transport, config, enabled, created_at, favorite "
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
            "favorite": bool(row[6]),
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


async def connect_server(
    config: Config, user_id: str, server_id: str, *, force: bool = False,
) -> MCPClient:  # type: ignore[name-defined]  # noqa: F821
    """Connect to an MCP server. Returns the active MCPClient.

    If the server is already connected and healthy, returns the existing
    client without reconnecting (avoids anyio cancel scope errors from
    cross-task disconnect). Pass force=True to reconnect anyway.
    """
    from lazyclaw.mcp.client import MCPClient

    # Reuse existing healthy connection (avoids cancel scope errors)
    if not force and server_id in _active_clients:
        existing = _active_clients[server_id]
        if existing.is_connected:
            logger.debug("MCP server %s already connected — reusing", server_id)
            return existing

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
    return await connect_server(config, user_id, server_id, force=True)


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
    """Disconnect all active MCP clients and cancel idle timers."""
    # Cancel all idle timers and clear state
    for timer in _idle_timers.values():
        timer.cancel()
    _idle_timers.clear()
    _favorite_server_ids.clear()
    _connect_locks.clear()
    _activity_versions.clear()

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

        # Skip if required env vars are missing
        import os as _os
        env_required = info.get("env_required", [])
        if env_required and not all(_os.environ.get(k) for k in env_required):
            logger.debug(
                "Skipping %s: missing env vars %s",
                name, [k for k in env_required if not _os.environ.get(k)],
            )
            continue

        # Determine transport type: Python module, npx, or remote URL
        if "module" in info:
            # Python module — check if importable
            try:
                __import__(info["module"])
            except ImportError:
                continue
            transport = "stdio"
            server_config = {
                "command": sys.executable,
                "args": ["-m", info["module"]],
            }
        elif "node" in info:
            # Local Node.js script — check if node is available
            import shutil
            if not shutil.which("node"):
                continue
            # Resolve path relative to project root (lazyclaw/mcp/../../)
            node_script = os.path.join(
                os.path.dirname(__file__), "..", "..", info["node"],
            )
            node_script = os.path.abspath(node_script)
            if not os.path.isfile(node_script):
                logger.debug("Skipping %s: script not found at %s", name, node_script)
                continue
            transport = "stdio"
            server_config = {
                "command": "node",
                "args": [node_script],
            }
        elif "npx" in info:
            # npm package — check if npx is available
            import shutil
            if not shutil.which("npx"):
                continue
            transport = "stdio"
            # Build env — strip keys that should use OAuth instead of API
            npx_env = dict(info.get("env", {}))
            for strip_key in info.get("strip_env", []):
                npx_env.setdefault(strip_key, "")  # Empty = unset for subprocess
            server_config = {
                "command": "npx",
                "args": [info["npx"]],
                "env": npx_env,
            }
        elif "remote" in info:
            # Remote MCP server (SSE or streamable HTTP)
            url = info["remote"]
            transport = "sse" if url.endswith("/sse") else "streamable_http"
            server_config = {"url": url}
        else:
            continue
        server_id = str(uuid4())
        encrypted_config = encrypt(json.dumps(server_config), key)

        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO mcp_connections (id, user_id, name, transport, config) "
                "VALUES (?, ?, ?, ?, ?)",
                (server_id, user_id, name, transport, encrypted_config),
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
    """Auto-register and register tools from all bundled MCPs.

    Three-tier loading:
    - **Favorites**: connect at boot, register real tools.
    - **Non-favorites with cache**: register lazy stubs (no subprocess).
    - **Non-favorites without cache**: connect once to cache schemas,
      disconnect, then register lazy stubs.

    Returns total number of tools registered.
    """
    global _registry_ref
    _registry_ref = registry

    from lazyclaw.mcp.bridge import (
        cache_tool_schemas,
        load_cached_schemas,
        register_mcp_tools,
        register_mcp_tools_lazy,
    )

    # Step 1: ensure DB entries exist
    await auto_register_bundled_mcps(config, user_id)

    # Step 2: query all enabled bundled MCPs with favorite status
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, favorite FROM mcp_connections "
            "WHERE user_id = ? AND enabled = 1 AND name IN ({})".format(
                ",".join("?" for _ in BUNDLED_MCPS)
            ),
            (user_id, *BUNDLED_MCPS.keys()),
        )
        servers = await rows.fetchall()

    # Pre-filter: skip MCPs whose runtime (module/npx) isn't available
    import os as _os
    import shutil

    def _is_available(name: str) -> bool:
        info = BUNDLED_MCPS.get(name, {})
        for key in info.get("env_required", []):
            if not _os.environ.get(key):
                return False
        if "module" in info:
            try:
                __import__(info["module"])
            except ImportError:
                return False
        elif "node" in info:
            if not shutil.which("node"):
                return False
            node_script = _os.path.join(
                _os.path.dirname(__file__), "..", "..", info["node"],
            )
            if not _os.path.isfile(_os.path.abspath(node_script)):
                return False
        elif "npx" in info:
            if not shutil.which("npx"):
                return False
        return True

    # Split into favorites vs lazy
    favorites: list[tuple[str, str]] = []
    lazy: list[tuple[str, str]] = []

    for row in servers:
        sid, name, fav = row[0], row[1], bool(row[2])
        if not _is_available(name):
            continue
        if fav:
            favorites.append((sid, name))
            _favorite_server_ids.add(sid)
        else:
            lazy.append((sid, name))

    total_tools = 0

    # -- Favorites: connect at boot in parallel (existing fast path) ----------

    async def _connect_favorite(server_id: str, name: str) -> int:
        if server_id in _active_clients:
            return 0
        try:
            info = BUNDLED_MCPS.get(name, {})
            if info.get("oauth"):
                client = await connect_server_with_oauth(config, user_id, server_id)
            else:
                client = await connect_server(config, user_id, server_id)
            tools = await client.list_tools()
            await cache_tool_schemas(config, name, tools)
            count = await register_mcp_tools(
                client, registry, config=config, user_id=user_id,
            )
            logger.info("Connected favorite MCP %s: %d tools", name, count)
            return count
        except Exception:
            logger.warning("Failed to connect favorite MCP %s", name, exc_info=True)
            return 0

    if favorites:
        results = await asyncio.gather(
            *(_connect_favorite(sid, name) for sid, name in favorites),
            return_exceptions=True,
        )
        total_tools += sum(r for r in results if isinstance(r, int))

    # -- Non-favorites: register lazy stubs from cache ------------------------

    need_cold_connect: list[tuple[str, str, bool]] = []  # (server_id, name, is_oauth)

    for server_id, name in lazy:
        info = BUNDLED_MCPS.get(name, {})
        is_oauth = bool(info.get("oauth"))

        cached = await load_cached_schemas(config, name)
        if cached is not None:
            # Cache hit — register lazy stubs directly (no subprocess)
            count = await register_mcp_tools_lazy(
                server_id, name, cached, registry,
                config=config, user_id=user_id, is_oauth=is_oauth,
            )
            total_tools += count
            continue

        # Skip OAuth and optional servers from cold-connect:
        # - OAuth requires browser interaction to authenticate
        # - Optional servers (stripe, jobspy) may hang downloading via npx
        # User must /mcp fav or /mcp connect to activate these.
        if is_oauth or info.get("optional"):
            logger.debug(
                "Skipping schema cache for %s MCP %s — "
                "use /mcp fav or /mcp connect to activate",
                "OAuth" if is_oauth else "optional", name,
            )
            continue

        need_cold_connect.append((server_id, name, is_oauth))

    # Cold-connect uncached servers in parallel with timeout
    async def _cold_cache(server_id: str, name: str, is_oauth: bool) -> int:
        try:
            client = await asyncio.wait_for(
                connect_server(config, user_id, server_id),
                timeout=15,
            )
            tools = await client.list_tools()
            await cache_tool_schemas(config, name, tools)
            await disconnect_server(user_id, server_id)

            cached = await load_cached_schemas(config, name)
            if cached:
                count = await register_mcp_tools_lazy(
                    server_id, name, cached, registry,
                    config=config, user_id=user_id, is_oauth=is_oauth,
                )
                logger.info("Cached + lazy-registered MCP %s", name)
                return count
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout caching MCP %s (>15s) — will retry on next startup",
                name,
            )
            await disconnect_server(user_id, server_id)
        except Exception:
            logger.warning(
                "Failed to cache MCP %s — tools unavailable until manual connect",
                name, exc_info=True,
            )
        return 0

    if need_cold_connect:
        cold_results = await asyncio.gather(
            *(_cold_cache(sid, n, oauth) for sid, n, oauth in need_cold_connect),
            return_exceptions=True,
        )
        total_tools += sum(r for r in cold_results if isinstance(r, int))

    logger.info(
        "MCP startup: %d favorites connected, %d lazy-registered, %d total tools",
        len(favorites), len(lazy), total_tools,
    )
    return total_tools
