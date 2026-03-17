"""Agent event callbacks for live process visibility."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

_null_logger = logging.getLogger(__name__)


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
        _null_logger.warning(
            "Approval request for '%s' auto-denied (no callback handler configured)",
            skill_name,
        )
        return False


class CancellationToken:
    """Cooperative cancellation signal for agent operations."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled
