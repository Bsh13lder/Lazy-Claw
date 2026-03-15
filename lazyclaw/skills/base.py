from __future__ import annotations

from abc import ABC, abstractmethod


class BaseSkill(ABC):
    """Base class for all LazyClaw skills."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def category(self) -> str:
        """Skill category for organization. Override in subclasses."""
        return "general"

    @property
    def permission_hint(self) -> str:
        """Suggested default permission: 'allow', 'ask', or 'deny'.
        Used when no user override or category default exists."""
        return "allow"

    @property
    @abstractmethod
    def parameters_schema(self) -> dict:
        """JSON Schema for the skill's parameters."""
        ...

    @abstractmethod
    async def execute(self, user_id: str, params: dict) -> str:
        """Execute the skill and return result as string."""
        ...

    def to_openai_tool(self) -> dict:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
