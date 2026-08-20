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
    def display_name(self) -> str:
        """Human-readable name for UI display. Override in subclasses."""
        return self.name

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
    def read_only(self) -> bool:
        """Whether this skill only reads data and never modifies state.

        Read-only skills (search, fetch, recall, status checks) are safe to
        execute concurrently. State-modifying skills (write, send, delete,
        create) must run sequentially. Defaults to False (conservative).
        """
        return False

    @property
    def parallel_safe(self) -> bool:
        """Whether sibling calls to this skill in ONE assistant message may
        run concurrently.

        This — not ``read_only`` — is the batching predicate for the TAOR
        loop and ``ToolExecutor.execute_batch``. It defaults to
        ``read_only`` (reads are trivially parallel-safe), but a skill can
        opt in without claiming to be a read: ``agent`` dispatch fans out
        by design (2026-08-20: 3 dispatches in one message ran strictly
        sequentially — 3x wall clock — because the old predicate was
        ``read_only``, which dispatch must never claim since that flag
        also feeds permission reasoning).
        """
        return self.read_only

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
