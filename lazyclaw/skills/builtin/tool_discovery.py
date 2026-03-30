"""Tool discovery meta-skill — LLM searches for tools by keyword.

Replaces regex-based tool selection. The LLM calls search_tools("browser email")
to discover what tools are available, then calls them directly. Only 3-4 base tool
schemas are sent upfront instead of 17+.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SearchToolsSkill(BaseSkill):
    """Let the LLM discover available tools by keyword search."""

    def __init__(self, registry=None) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "search_tools"

    @property
    def display_name(self) -> str:
        return "Search Tools"

    @property
    def description(self) -> str:
        return (
            "Search for available tools by keyword. Returns tool names and descriptions. "
            "You only see ~16 base tools — use this to discover the rest. "
            "Examples: search_tools('whatsapp'), search_tools('email'), "
            "search_tools('instagram'), search_tools('task'), search_tools('vault'), "
            "search_tools('job'), search_tools('mcp'), search_tools('permission')"
        )

    @property
    def category(self) -> str:
        return "core"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to search for tools (e.g. 'browser', 'job search', 'memory', 'email')",
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        query = params.get("query", "").lower()
        if not query:
            return "Provide a search query to find tools."

        if self._registry is None:
            return "No tools available."

        keywords = query.split()
        all_tools = self._registry.list_core_tools() + self._registry.list_mcp_tools()

        scored: list[tuple[int, str, str]] = []
        for tool in all_tools:
            func = tool.get("function", {})
            tool_name = func.get("name", "")
            tool_desc = func.get("description", "")

            if tool_name == "search_tools":
                continue

            text = f"{tool_name} {tool_desc}".lower()
            score = sum(1 for kw in keywords if kw in text)

            if score > 0:
                scored.append((score, tool_name, tool_desc[:120]))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:8]

        if not top:
            return f"No tools found for '{query}'. Try different keywords."

        lines = [f"Found {len(top)} tools:"]
        for _, name, desc in top:
            lines.append(f"- **{name}**: {desc}")

        return "\n".join(lines)
