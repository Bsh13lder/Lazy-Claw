"""Agent event callbacks for live process visibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AgentEvent:
    """A single event fired during agent processing."""

    kind: str  # llm_call, tool_call, tool_result, team_delegate, approval, token, done
    detail: str  # Human-readable description
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class AgentCallback(Protocol):
    async def on_event(self, event: AgentEvent) -> None: ...

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool: ...


class NullCallback:
    """Default no-op callback — used when no listener is attached."""

    async def on_event(self, event: AgentEvent) -> None:
        pass

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        return False
