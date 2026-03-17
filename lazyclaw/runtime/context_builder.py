"""System prompt builder with agent self-awareness.

Assembles: personality (SOUL.md) + capabilities (skills, MCP, config) + memories.
The capabilities section gives the agent knowledge of its own tools and services.
"""

from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.runtime.personality import load_personality

logger = logging.getLogger(__name__)


async def build_context(
    config: Config,
    user_id: str,
    registry=None,
) -> str:
    """Build system prompt with personality + capabilities + memories."""
    personality = load_personality()

    # 1. Capabilities — what the agent can do
    capabilities = await _build_capabilities_section(config, user_id, registry)

    # 2. Personal memories
    from lazyclaw.memory.personal import get_memories

    memories = await get_memories(config, user_id, limit=10)

    # Combine sections
    sections = [personality]
    if capabilities:
        sections.append(capabilities)
    if memories:
        lines = [f"- {m['content']}" for m in memories]
        sections.append(
            "## What I know about you\n" + "\n".join(lines)
        )

    return "\n\n---\n\n".join(sections)


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
                continue  # MCP tools listed separately below
            display_names = []
            for name in skill_names:
                display_names.append(registry.get_display_name(name))
            cat_label = _category_label(cat)
            lines.append(f"**{cat_label}:** {', '.join(display_names)}")
        lines.append("")

    # Connected MCP servers
    mcp_lines = await _get_mcp_status(config, user_id)
    if mcp_lines:
        lines.append(f"**MCP Servers Connected ({len(mcp_lines)}):**")
        for mcp_line in mcp_lines:
            lines.append(f"  - {mcp_line}")
        lines.append("")

    # Current config
    config_parts = [f"Model: {config.default_model}"]

    try:
        from lazyclaw.llm.eco_settings import get_eco_settings
        eco = await get_eco_settings(config, user_id)
        eco_mode = eco.get("eco_mode", "full")
        config_parts.append(f"ECO: {eco_mode}")
    except Exception:
        pass

    try:
        from lazyclaw.teams.settings import get_team_settings
        team = await get_team_settings(config, user_id)
        team_mode = team.get("mode", "never")
        config_parts.append(f"Team: {team_mode}")
    except Exception:
        pass

    lines.append(f"**Current Config:** {' | '.join(config_parts)}")

    return "\n".join(lines)


async def _get_mcp_status(config: Config, user_id: str) -> list[str]:
    """Get connected MCP server names and tool counts."""
    try:
        from lazyclaw.mcp.manager import _active_clients, BUNDLED_MCPS
        from lazyclaw.db.connection import db_session

        if not _active_clients:
            return []

        # Get server names from DB
        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id, name FROM mcp_connections WHERE user_id = ?",
                (user_id,),
            )
            server_map = {row[0]: row[1] for row in await rows.fetchall()}

        result = []
        for server_id, client in _active_clients.items():
            name = server_map.get(server_id, client.name)
            desc = BUNDLED_MCPS.get(name, {}).get("description", "")
            try:
                tools = await client.list_tools()
                tool_count = len(tools)
            except Exception:
                tool_count = 0
            entry = f"{name}: {desc}" if desc else name
            entry += f" ({tool_count} tools)"
            result.append(entry)

        return result
    except Exception as exc:
        logger.debug("Failed to get MCP status: %s", exc)
        return []


def _category_label(cat: str) -> str:
    """Human-readable category label."""
    labels = {
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
    }
    return labels.get(cat, cat.title())
