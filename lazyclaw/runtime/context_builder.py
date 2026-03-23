"""System prompt builder with agent self-awareness.

Assembles: personality (SOUL.md) + capabilities (skills, MCP, config) + memories.
Capabilities are cached (60s TTL) to avoid per-message MCP RPC overhead.
"""

from __future__ import annotations

import logging
import time

from lazyclaw.config import Config
from lazyclaw.runtime.personality import load_personality

logger = logging.getLogger(__name__)

# Cache for capabilities section (60s TTL)
_capabilities_cache: str = ""
_capabilities_time: float = 0.0
_CAPABILITIES_TTL = 60.0

# Cache for MCP status (60s TTL)
_mcp_cache: list[str] = []
_mcp_cache_time: float = 0.0


async def build_context(
    config: Config,
    user_id: str,
    registry=None,
) -> str:
    """Build system prompt with personality + capabilities + memories."""
    personality = load_personality()

    # 1. Capabilities (cached 60s)
    capabilities = await _build_capabilities_cached(config, user_id, registry)

    # 2. Personal memories
    from lazyclaw.memory.personal import get_memories

    memories = await get_memories(config, user_id, limit=10)

    # 3. Recent activity (daily/weekly logs — agent's "diary")
    activity_section = ""
    try:
        from lazyclaw.memory.daily_log import list_daily_logs

        recent_logs = await list_daily_logs(config, user_id, limit=10)
        if recent_logs:
            log_lines = []
            for log in reversed(recent_logs):  # oldest first
                if log["date"].endswith("_week"):
                    log_lines.append(f"**Week of {log['date'][:10]}:** {log['summary'][:250]}")
                elif log["date"].endswith("_month"):
                    log_lines.append(f"**Month {log['date'][:7]}:** {log['summary'][:200]}")
                else:
                    log_lines.append(f"**{log['date']}:** {log['summary'][:150]}")
            activity_section = "## Recent Activity\n" + "\n".join(log_lines)
    except Exception:
        pass

    # Combine sections
    sections = [personality]
    if capabilities:
        sections.append(capabilities)
    if activity_section:
        sections.append(activity_section)
    if memories:
        lines = [f"- {m['content']}" for m in memories]
        sections.append(
            "## What I know about you\n" + "\n".join(lines)
        )

    return "\n\n---\n\n".join(sections)


async def _build_capabilities_cached(
    config: Config,
    user_id: str,
    registry=None,
) -> str:
    """Build capabilities section with 60s TTL cache."""
    global _capabilities_cache, _capabilities_time

    now = time.monotonic()
    if _capabilities_cache and (now - _capabilities_time) < _CAPABILITIES_TTL:
        return _capabilities_cache

    result = await _build_capabilities_section(config, user_id, registry)
    _capabilities_cache = result
    _capabilities_time = now
    return result


def invalidate_capabilities_cache() -> None:
    """Call when skills or MCP servers change to force rebuild."""
    global _capabilities_cache, _capabilities_time, _mcp_cache, _mcp_cache_time
    _capabilities_cache = ""
    _capabilities_time = 0.0
    _mcp_cache = []
    _mcp_cache_time = 0.0


async def _build_capabilities_section(
    config: Config,
    user_id: str,
    registry=None,
) -> str:
    """Build the capabilities section showing available tools and services."""
    lines = [
        "## Your Capabilities",
        "",
        "You are LazyClaw, an E2E encrypted AI agent platform. "
        "Here is what you have available right now:",
        "",
    ]

    # Skills by category
    if registry is not None:
        categories = registry.list_by_category()
        for cat, skill_names in sorted(categories.items()):
            if cat == "mcp":
                continue
            display_names = [
                registry.get_display_name(name) for name in skill_names
            ]
            lines.append(f"**{_category_label(cat)}:** {', '.join(display_names)}")
        lines.append("")

    # Connected MCP servers (cached separately)
    mcp_lines = await _get_mcp_status_cached(config, user_id)
    if mcp_lines:
        lines.append(f"**MCP Servers Connected ({len(mcp_lines)}):**")
        for mcp_line in mcp_lines:
            lines.append(f"  - {mcp_line}")
        lines.append("")

    # Current config
    config_parts = [f"Model: {config.brain_model}"]

    eco_mode = "full"
    try:
        from lazyclaw.llm.eco_settings import get_eco_settings
        eco = await get_eco_settings(config, user_id)
        eco_mode = eco.get("mode", "full")
        config_parts.append(f"ECO: {eco_mode}")
    except Exception:
        pass

    try:
        from lazyclaw.teams.settings import get_team_settings
        team = await get_team_settings(config, user_id)
        config_parts.append(f"Team: {team.get('mode', 'never')}")
    except Exception:
        pass

    # Ollama status (for local/hybrid modes)
    ollama_status = ""
    try:
        from lazyclaw.llm.providers.ollama_provider import OllamaProvider
        provider = OllamaProvider()
        if await provider.health_check():
            running = await provider.list_running()
            if running:
                model_names = [m["name"] for m in running]
                ollama_status = f"running ({', '.join(model_names)})"
            else:
                ollama_status = "running (no models loaded)"
        else:
            ollama_status = "not running"
        await provider.close()
    except Exception:
        ollama_status = "unavailable"

    lines.append(f"**Config:** {' | '.join(config_parts)} | Ollama: {ollama_status}")
    lines.append(f"  ECO modes: local/eco/hybrid/full (current: {eco_mode}). Change: eco_set_mode")
    lines.append("  Direct channels (no browser): Instagram, WhatsApp, Email — prefer over browser")

    return "\n".join(lines)


async def _get_mcp_status_cached(config: Config, user_id: str) -> list[str]:
    """Get MCP status with 60s TTL cache (avoids ListToolsRequest spam)."""
    global _mcp_cache, _mcp_cache_time

    now = time.monotonic()
    if _mcp_cache and (now - _mcp_cache_time) < _CAPABILITIES_TTL:
        return _mcp_cache

    result = await _get_mcp_status(config, user_id)
    _mcp_cache = result
    _mcp_cache_time = now
    return result


async def _get_mcp_status(config: Config, user_id: str) -> list[str]:
    """Query connected MCP server names and tool counts (uncached)."""
    try:
        from lazyclaw.mcp.bridge import load_cached_schemas
        from lazyclaw.mcp.manager import _active_clients, BUNDLED_MCPS
        from lazyclaw.db.connection import db_session
        import json as _json

        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id, name, favorite FROM mcp_connections WHERE user_id = ?",
                (user_id,),
            )
            all_servers = [(row[0], row[1], bool(row[2])) for row in await rows.fetchall()]

        if not all_servers:
            return []

        result = []
        for server_id, name, is_fav in all_servers:
            desc = BUNDLED_MCPS.get(name, {}).get("description", "")

            # Get tool count: from live client or cache
            tool_count = 0
            if server_id in _active_clients:
                try:
                    tools = await _active_clients[server_id].list_tools()
                    tool_count = len(tools)
                except Exception:
                    tool_count = 0
                status = "connected"
            else:
                cached = await load_cached_schemas(config, name)
                if cached:
                    try:
                        tool_count = len(_json.loads(cached))
                    except Exception:
                        pass
                status = "idle (lazy)"

            entry = f"{name}: {desc}" if desc else name
            entry += f" ({tool_count} tools, {status})"
            if is_fav:
                entry += " [favorite]"
            result.append(entry)

        return result
    except Exception as exc:
        logger.debug("Failed to get MCP status: %s", exc)
        return []


def _category_label(cat: str) -> str:
    """Human-readable category label."""
    return {
        "general": "Core Skills",
        "utility": "Utilities",
        "search": "Search",
        "research": "Research",
        "memory": "Memory",
        "vault": "Vault",
        "browser": "Browser",
        "computer": "Computer",
        "skills": "Skills Management",
        "custom": "Custom Skills",
        "security": "Security",
    }.get(cat, cat.title())
