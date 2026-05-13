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


def reinject_mcp_tools(
    registry: Any,
    tools: list[dict[str, Any]],
    suppressed_tool_names: Iterable[str] | None = None,
) -> list[str]:
    """Scan ``registry.list_mcp_tools()`` and append schemas missing from ``tools``.

    Mutates ``tools`` in place. Returns the list of tool names newly added
    so the caller can log them. Tool names already present in ``tools`` or
    in ``suppressed_tool_names`` are skipped — the AUTO-PROMOTE narrowing
    path uses suppressed_tool_names to forbid certain tools and we must
    respect that.

    Safe to call when ``registry`` is None or empty — returns ``[]``.
    Any registry exception is logged and swallowed; the caller's flow
    must not break because reinjection failed.
    """
    if registry is None:
        return []
    suppressed: set[str] = set(suppressed_tool_names or ())
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
            schema = registry.get_tool_schema(name)
            if schema is None:
                continue
            tools.append(schema)
            existing_names.add(name)
            newly_injected.append(name)
        return newly_injected
    except Exception:
        logger.warning(
            "reinject_mcp_tools failed; returning empty list",
            exc_info=True,
        )
        return []
