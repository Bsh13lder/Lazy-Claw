"""Post-execution helper for connect_mcp_server / install_mcp_server.

When the brain calls a tool that registers new MCP tools in the global
SkillRegistry, the per-turn `tools` list passed to the next LLM iteration
needs to learn about them — otherwise the brain has just connected a
server it can't actually use in this turn.

Pulled out of ``agent.process_message`` so it can be unit-tested without
spinning up the whole agent.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def snapshot_mcp_tool_names(registry: Any) -> set[str]:
    """Capture the registry's MCP tool names at a point in time.

    Pair with ``reinject_mcp_tools(pre_connect_tool_names=...)`` to inject
    only the delta a single ``connect_mcp_server`` / ``install_mcp_server``
    call added — preventing the post-hook from dragging in every other
    already-registered MCP's tools just because they weren't part of the
    per-turn curated ``tools`` list.
    """
    if registry is None:
        return set()
    try:
        names: set[str] = set()
        for tool_info in registry.list_mcp_tools():
            name = (tool_info.get("function") or {}).get("name")
            if name:
                names.add(name)
        logger.debug("[mcpreinject] snapshot captured %d MCP tool name(s)", len(names))
        return names
    except Exception:
        logger.warning("[mcpreinject] snapshot_mcp_tool_names failed", exc_info=True)
        return set()


def reinject_mcp_tools(
    registry: Any,
    tools: list[dict[str, Any]],
    suppressed_tool_names: Iterable[str] | None = None,
    pre_connect_tool_names: Iterable[str] | None = None,
) -> list[str]:
    """Scan ``registry.list_mcp_tools()`` and append schemas missing from ``tools``.

    Mutates ``tools`` in place. Returns the list of tool names newly added
    so the caller can log them. Tool names already present in ``tools`` or
    in ``suppressed_tool_names`` are skipped — the AUTO-PROMOTE narrowing
    path uses suppressed_tool_names to forbid certain tools and we must
    respect that.

    When ``pre_connect_tool_names`` is provided (a snapshot of the
    registry's MCP tool names taken just before the triggering
    ``connect_mcp_server`` / ``install_mcp_server`` call), only names
    present in the registry NOW but NOT in the snapshot are eligible —
    that's the true delta of "just-connected MCP's tools." Without this
    filter, a connect on one MCP would resurrect every other connected
    MCP's tools that the per-turn channel-keyword router intentionally
    left out, blowing the tool surface from ~25 to 160+.

    Safe to call when ``registry`` is None or empty — returns ``[]``.
    Any registry exception is logged and swallowed; the caller's flow
    must not break because reinjection failed.
    """
    if registry is None:
        return []
    suppressed: set[str] = set(suppressed_tool_names or ())
    pre_connect: set[str] | None = (
        set(pre_connect_tool_names)
        if pre_connect_tool_names is not None
        else None
    )
    try:
        existing_names: set[str] = {
            (t.get("function") or {}).get("name")
            for t in tools
        }
        # Drop any None entries (malformed schemas)
        existing_names.discard(None)

        newly_injected: list[str] = []
        for tool_info in registry.list_mcp_tools():
            func = tool_info.get("function") or {}
            name = func.get("name")
            if not name:
                continue
            if name in existing_names:
                continue
            if name in suppressed:
                continue
            # Delta filter: only consider tools that appeared AFTER the
            # snapshot was taken — i.e. genuinely registered by the
            # triggering connect/install call. Older tools from other
            # MCPs are intentionally left out of the curated per-turn
            # `tools` list and should stay out.
            if pre_connect is not None and name in pre_connect:
                continue
            schema = registry.get_tool_schema(name)
            if schema is None:
                continue
            tools.append(schema)
            existing_names.add(name)
            newly_injected.append(name)
        logger.debug(
            "[mcpreinject] reinjected %d tool(s): %s",
            len(newly_injected), newly_injected,
        )
        return newly_injected
    except Exception:
        logger.warning(
            "[mcpreinject] reinject_mcp_tools failed; returning empty list",
            exc_info=True,
        )
        return []
