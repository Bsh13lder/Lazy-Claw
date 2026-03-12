from __future__ import annotations
from lazyclaw.skills.base import BaseSkill


class MemorySaveSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a fact, preference, or piece of context about the user for future reference. "
            "Use this when the user shares personal information, preferences, or important context "
            "that should be remembered across conversations."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact or preference to remember (e.g., 'User's name is Alex', 'Prefers Python over JavaScript')",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "preference", "context"],
                    "description": "Type of memory: fact (personal info), preference (likes/dislikes), context (situational)",
                    "default": "fact",
                },
                "importance": {
                    "type": "integer",
                    "description": "Importance 1-10 (10=critical like name, 1=trivial)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Memory system not configured"
        from lazyclaw.memory.personal import save_memory
        content = params["content"]
        memory_type = params.get("memory_type", "fact")
        importance = params.get("importance", 5)
        await save_memory(self._config, user_id, content, memory_type, importance)
        return f"Saved: {content}"
