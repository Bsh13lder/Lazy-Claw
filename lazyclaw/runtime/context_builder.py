"""System prompt builder with agent self-awareness.

Assembles: personality (SOUL.md) + capabilities (skills, MCP, config) + memories.
Capabilities are cached (60s TTL) to avoid per-message MCP RPC overhead.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from lazyclaw.config import Config
from lazyclaw.runtime.personality import load_personality

logger = logging.getLogger(__name__)

# Patterns that indicate error/debug details — strip from activity summaries
# to prevent the LLM from hallucinating about past technical issues.
_ERROR_NOISE_RE = re.compile(
    r"((?:HTTP\s*)?\b[45]\d{2}\b"        # HTTP 401, 500, etc.
    r"|error|exception|traceback"
    r"|unauthorized|forbidden"
    r"|rate.?limit|timeout"
    r"|failed|failure|crashed"
    r"|stack.?trace|ECONNREFUSED"
    r"|api.?key|credentials?.?issue)",
    re.IGNORECASE,
)


def _sanitize_activity_summary(summary: str) -> str:
    """Remove error/debug noise from activity summaries.

    Sentences mentioning HTTP errors, stack traces, auth failures, etc.
    are stripped so the LLM doesn't hallucinate about past issues.
    """
    if not summary:
        return ""
    # Split into sentences, keep only clean ones
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    clean = [s for s in sentences if not _ERROR_NOISE_RE.search(s)]
    return " ".join(clean).strip()

# Cache for capabilities section (60s TTL)
_capabilities_cache: str = ""
_capabilities_time: float = 0.0
_CAPABILITIES_TTL = 60.0

# Cache for MCP status (60s TTL)
_mcp_cache: list[str] = []
_mcp_cache_time: float = 0.0

# Lock protecting both caches against concurrent async rebuilds
_cache_lock = asyncio.Lock()


async def build_context(
    config: Config,
    user_id: str,
    registry=None,
    channel_id: str | None = None,
    project_id: str | None = None,
) -> str:
    """Build system prompt with personality + capabilities + memories.

    Args:
        config: App config.
        user_id: Current user identifier.
        registry: Skill registry (for capabilities section).
        channel_id: Active channel (loads CHANNEL memory layer when provided).
        project_id: Active project (loads PROJECT memory layer when provided).
    """
    personality = load_personality()

    # 1. Capabilities (cached 60s)
    capabilities = await _build_capabilities_cached(config, user_id, registry)

    # 2. Multi-layer memory context (Global → Project → Channel → User)
    #    Injected before activity logs so layered context informs interpretation
    #    of recent events. Synchronous file I/O — fast for small markdown files.
    layer_context = ""
    try:
        from lazyclaw.memory.layers import load_session_context
        layer_context = load_session_context(
            config, user_id,
            channel_id=channel_id,
            project_id=project_id,
        )
    except Exception:
        pass

    # 3. Personal memories (DB-backed encrypted facts — backward compat)
    from lazyclaw.memory.personal import get_memories

    memories = await get_memories(config, user_id, limit=10)

    # 4. Recent activity (daily/weekly logs — agent's "diary")
    # Summaries are sanitized to remove error details that could cause
    # the LLM to hallucinate about past issues (e.g. "401 errors").
    activity_section = ""
    try:
        from lazyclaw.memory.daily_log import list_daily_logs

        recent_logs = await list_daily_logs(config, user_id, limit=10)
        if recent_logs:
            log_lines = []
            for log in reversed(recent_logs):  # oldest first
                summary = _sanitize_activity_summary(log.get("summary", ""))
                if not summary:
                    continue
                if log["date"].endswith("_week"):
                    log_lines.append(f"**Week of {log['date'][:10]}:** {summary[:250]}")
                elif log["date"].endswith("_month"):
                    log_lines.append(f"**Month {log['date'][:7]}:** {summary[:200]}")
                else:
                    log_lines.append(f"**{log['date']}:** {summary[:150]}")
            if log_lines:
                activity_section = "## Recent Activity\n" + "\n".join(log_lines)
    except Exception:
        pass

    # Combine sections — layered context between capabilities and activity
    sections = [personality]
    if capabilities:
        sections.append(capabilities)
    if layer_context:
        sections.append(layer_context)
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

    # Fast path (no lock) — return cached if still valid
    now = time.monotonic()
    if _capabilities_cache and (now - _capabilities_time) < _CAPABILITIES_TTL:
        return _capabilities_cache

    # Slow path — acquire lock with timeout to avoid deadlock from stuck MCP
    try:
        acquired = await asyncio.wait_for(_cache_lock.acquire(), timeout=3)
    except asyncio.TimeoutError:
        logger.warning("Capabilities cache lock held >3s — returning stale or empty")
        return _capabilities_cache or ""
    try:
        now = time.monotonic()
        if _capabilities_cache and (now - _capabilities_time) < _CAPABILITIES_TTL:
            return _capabilities_cache
        result = await asyncio.wait_for(
            _build_capabilities_section(config, user_id, registry),
            timeout=10,
        )
        _capabilities_cache = result
        _capabilities_time = now
        return result
    except asyncio.TimeoutError:
        logger.warning("_build_capabilities_section timed out (>10s)")
        return _capabilities_cache or ""
    finally:
        _cache_lock.release()


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

    # Current config — show ECO-resolved models
    eco_mode = "hybrid"
    _brain_display = config.brain_model
    try:
        from lazyclaw.llm.eco_settings import get_eco_settings
        from lazyclaw.llm.model_registry import get_mode_models
        eco = await get_eco_settings(config, user_id)
        eco_mode = eco.get("mode", "hybrid")
        _m = get_mode_models(eco_mode)
        _brain_display = eco.get("brain_model") or _m["brain"]
    except Exception:
        pass
    config_parts = [f"Model: {_brain_display}"]

    try:
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
    lines.append(f"  ECO modes: hybrid/full (current: {eco_mode}). Change: eco_set_mode")
    lines.append("  Direct channels (no browser): Instagram, WhatsApp, Email — prefer over browser")

    return "\n".join(lines)


async def _get_mcp_status_cached(config: Config, user_id: str) -> list[str]:
    """Get MCP status with 60s TTL cache (avoids ListToolsRequest spam)."""
    global _mcp_cache, _mcp_cache_time

    # Fast path (no lock)
    now = time.monotonic()
    if _mcp_cache and (now - _mcp_cache_time) < _CAPABILITIES_TTL:
        return _mcp_cache

    # Slow path — lock with timeout, rebuild
    try:
        await asyncio.wait_for(_cache_lock.acquire(), timeout=3)
    except asyncio.TimeoutError:
        logger.warning("MCP cache lock held >3s — returning stale")
        return _mcp_cache
    try:
        now = time.monotonic()
        if _mcp_cache and (now - _mcp_cache_time) < _CAPABILITIES_TTL:
            return _mcp_cache
        result = await asyncio.wait_for(
            _get_mcp_status(config, user_id),
            timeout=8,
        )
        _mcp_cache = result
        _mcp_cache_time = now
        return result
    except asyncio.TimeoutError:
        logger.warning("_get_mcp_status timed out (>8s)")
        return _mcp_cache
    finally:
        _cache_lock.release()


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
                    tools = await asyncio.wait_for(
                        _active_clients[server_id].list_tools(),
                        timeout=5,
                    )
                    tool_count = len(tools)
                except (asyncio.TimeoutError, Exception):
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
