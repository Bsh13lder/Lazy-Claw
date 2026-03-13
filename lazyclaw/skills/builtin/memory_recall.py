from __future__ import annotations
from lazyclaw.skills.base import BaseSkill


class MemoryRecallSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "recall_memories"

    @property
    def description(self) -> str:
        return (
            "Search your memories about the user. Use this to recall specific facts, "
            "preferences, or context you've previously saved about them."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for (e.g., 'name', 'programming language', 'work')",
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Memory system not configured"
        from lazyclaw.memory.personal import search_memories
        query = params["query"]
        memories = await search_memories(self._config, user_id, query)
        if not memories:
            return f"No memories found matching '{query}'"
        lines = []
        for m in memories:
            lines.append(f"- [{m['type']}] {m['content']} (importance: {m['importance']})")
        return "\n".join(lines)
