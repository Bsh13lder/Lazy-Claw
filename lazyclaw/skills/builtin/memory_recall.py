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

        vault_keys = await _vault_keys_safe(self._config, user_id)

        all_mem = await get_memories(self._config, user_id, limit=10)
        if not all_mem:
            base = f"No direct match for '{query}' and no memories stored yet."
            if vault_keys:
                return (
                    f"{base} Vault contains {len(vault_keys)} credentials "
                    f"(names only): {', '.join(vault_keys)}. "
                    f"If the user is asking about a credential, call "
                    f"`vault_get(key=...)` instead of retrying recall_memories."
                )
            return f"{base} Nothing to search."

        lines = [
            f"No direct match for '{query}'. Here are the 10 most recent memories "
            f"— the one you want might be phrased differently:"
        ]
        for m in all_mem:
            mtype = m.get("type") or m.get("memory_type") or "?"
            lines.append(
                f"- [{mtype}] {m['content']} (id: {m['id']})"
            )
        if vault_keys:
            lines.append("")
            lines.append(
                f"Vault keys (names only, values encrypted): "
                f"{', '.join(vault_keys)}"
            )
            lines.append(
                "If the user is asking about a credential/API key/OAuth secret, "
                "call `vault_get(key=...)` — credentials are NEVER in personal memory."
            )
        lines.append(
            "\nSTOP. Do not retry recall_memories with other keywords — the "
            "full list above is everything stored. If it's not here, it's not "
            "in memory."
        )
        return "\n".join(lines)


async def _vault_keys_safe(config, user_id: str) -> list[str]:
    try:
        from lazyclaw.crypto.vault import list_credentials
        return await list_credentials(config, user_id)
    except Exception:
        return []
