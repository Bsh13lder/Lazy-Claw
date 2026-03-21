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

    async def on_help_request(
        self, context: str, needs_browser: bool
    ) -> str:
        """Ask the user for help when the agent is stuck.

        Returns: "ready" (user will take over browser), "done" (user finished),
        "skip" (no UI / auto-skip), or free-text instruction.
        Waits indefinitely — no timeout.
        """
        ...


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

    async def on_help_request(
        self, context: str, needs_browser: bool
    ) -> str:
        _null_logger.warning(
            "Help request auto-skipped (no callback handler): %s", context,
        )
        return "skip"


class MultiCallback:
    """Forward events to multiple callbacks."""

    def __init__(self, *callbacks: AgentCallback) -> None:
        self._callbacks = callbacks

    async def on_event(self, event: AgentEvent) -> None:
        for cb in self._callbacks:
            try:
                await cb.on_event(event)
            except Exception:
                _null_logger.debug(
                    "MultiCallback: %s.on_event(%s) failed",
                    type(cb).__name__, event.kind, exc_info=True,
                )

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

    async def on_help_request(
        self, context: str, needs_browser: bool
    ) -> str:
        for cb in self._callbacks:
            try:
                result = await cb.on_help_request(context, needs_browser)
                if result != "skip":
                    return result
            except Exception:
                _null_logger.warning(
                    "MultiCallback: %s.on_help_request failed",
                    type(cb).__name__, exc_info=True,
                )
        return "skip"


class CancellationToken:
    """Cooperative cancellation signal for agent operations."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled
