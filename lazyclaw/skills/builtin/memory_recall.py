from __future__ import annotations
from lazyclaw.skills.base import BaseSkill


class MemoryRecallSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def read_only(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "recall_memories"

    @property
    def description(self) -> str:
        return (
            "Search the user's saved memories. If no direct substring match, "
            "returns a preview of the most recent memories so you can see what's "
            "stored (prevents pointless retry loops). The memory might be "
            "phrased differently than your query."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to search for (e.g., 'name', 'timezone', 'google'). "
                        "Substring match, case-insensitive."
                    ),
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Memory system not configured"
        from lazyclaw.memory.personal import search_memories, get_memories

        query = ((params or {}).get("query") or "").strip()
        if not query:
            return "Error: `query` is required."

        memories = await search_memories(self._config, user_id, query)
        if memories:
            lines = [f"Matches for '{query}' ({len(memories)}):"]
            for m in memories:
                mtype = m.get("type") or m.get("memory_type") or "?"
                lines.append(
                    f"- [{mtype}] {m['content']} "
                    f"(importance: {m['importance']}, id: {m['id']})"
                )
            return "\n".join(lines)

        # No substring match — return a preview of recent memories so the
        # agent can see what's actually stored instead of looping.
        all_mem = await get_memories(self._config, user_id, limit=10)
        if not all_mem:
            return (
                f"No direct match for '{query}' and no memories stored yet. "
                f"Nothing to search."
            )
        lines = [
            f"No direct match for '{query}'. Here are the 10 most recent memories "
            f"— the one you want might be phrased differently:"
        ]
        for m in all_mem:
            mtype = m.get("type") or m.get("memory_type") or "?"
            lines.append(
                f"- [{mtype}] {m['content']} (id: {m['id']})"
            )
        lines.append(
            "\nIf the memory isn't here, it's not in personal_memory. "
            "Do NOT retry recall_memories with different keywords — "
            "it's either here or it's not."
        )
        return "\n".join(lines)
