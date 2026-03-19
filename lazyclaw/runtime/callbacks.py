"""Agent event callbacks for live process visibility."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

_null_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentEvent:
    """A single event fired during agent processing.

    Kinds: llm_call, tool_call, tool_result, team_delegate, approval,
    token, done, work_summary, specialist_*, team_*, attachment.

    The ``attachment`` kind carries binary data in metadata:
      - ``data``: raw bytes
      - ``media_type``: MIME type (e.g. "image/png")
      - ``filename``: optional filename
    """

    kind: str
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


class MultiCallback:
    """Forward events to multiple callbacks."""

    def __init__(self, *callbacks: AgentCallback) -> None:
        self._callbacks = callbacks

    async def on_event(self, event: AgentEvent) -> None:
        for cb in self._callbacks:
            try:
                await cb.on_event(event)
            except Exception:
                pass

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        for cb in self._callbacks:
            try:
                if await cb.on_approval_request(skill_name, arguments):
                    return True
            except Exception:
                pass
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
