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
        self._cancel_token = None

    @property
    def cancel_token(self):
        return self._cancel_token

    @cancel_token.setter
    def cancel_token(self, token):
        self._cancel_token = token
        for cb in self._callbacks:
            if hasattr(cb, "cancel_token"):
                cb.cancel_token = token

    async def on_event(self, event: AgentEvent) -> None:
        _null_logger.debug(
            "[callbacks] MultiCallback on_event kind=%s fanout=%d",
            event.kind, len(self._callbacks),
        )
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
        _null_logger.debug(
            "[callbacks] MultiCallback on_approval_request skill=%s fanout=%d",
            skill_name, len(self._callbacks),
        )
        for cb in self._callbacks:
            try:
                if await cb.on_approval_request(skill_name, arguments):
                    return True
            except Exception:
                _null_logger.warning(
                    "MultiCallback: %s.on_approval_request failed",
                    type(cb).__name__, exc_info=True,
                )
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


class StepTrackingCallback:
    """Forwards every event to the inner callback and, on each
    ``specialist_tool`` event, also updates the TeamLead step counter.

    Used by DelegateSkill where the specialist runs in the FOREGROUND
    lane and the user is expected to see the inline progress in chat.
    Background subagents must use :class:`SilentSubagentCallback` instead
    so their per-step output doesn't bleed into the chat WebSocket.
    """

    def __init__(self, inner, team_lead, task_id: str) -> None:
        self._inner = inner
        self._team_lead = team_lead
        self._task_id = task_id

    @property
    def cancel_token(self):
        return getattr(self._inner, "cancel_token", None)

    @cancel_token.setter
    def cancel_token(self, token):
        if hasattr(self._inner, "cancel_token"):
            self._inner.cancel_token = token

    async def on_event(self, event: AgentEvent) -> None:
        _null_logger.debug(
            "[callbacks] StepTrackingCallback on_event kind=%s task=%s",
            event.kind, self._task_id,
        )
        if event.kind == "specialist_tool":
            tool_name = (
                event.metadata.get("tool", event.detail)
                if event.metadata else event.detail
            )
            try:
                self._team_lead.update_step(self._task_id, tool_name)
            except Exception:
                _null_logger.debug(
                    "update_step failed for %s", self._task_id, exc_info=True,
                )
        await self._inner.on_event(event)

    async def on_approval_request(self, skill_name: str, arguments: dict) -> bool:
        return await self._inner.on_approval_request(skill_name, arguments)

    async def on_help_request(self, context: str, needs_browser: bool) -> str:
        return await self._inner.on_help_request(context, needs_browser)


class SilentSubagentCallback:
    """Background-subagent callback — drives TeamLead only, never chat.

    Architectural rule: a parallel subagent dispatched via
    ``dispatch_subagents`` must be *invisible* to the foreground chat
    stream. Its progress belongs in the Activity panel (lane='subagent'),
    fed by TeamLead → task_event_bus, NOT in the chat transcript next to
    the brain's own tokens / tool_calls.

    Why: the brain's ``WebSocketCallback`` writes every event back over
    the chat WS. If we forwarded subagent events to it we'd leak
    ``specialist_thinking`` / ``specialist_tool`` / ``phase`` /
    ``thinking_delta`` / ``token`` frames into the user's chat window
    while the brain is otherwise idle, breaking the "Started N subagents
    + one consolidated summary" contract.

    What we still do:
    - Mirror ``specialist_tool`` events into ``team_lead.update_step`` so
      the Activity panel shows live tool progress (the panel subscribes
      to ``task_event_bus`` and sees the resulting ``task_step`` frame).
    - Mirror ``phase`` events into ``team_lead.update_phase`` for the
      same reason — TAOR phase chips on the subagent card.
    - Auto-deny approval prompts and "skip" help requests; a background
      subagent can't pause and ask the user.

    Everything else is dropped on the floor.
    """

    def __init__(self, team_lead, task_id: str, cancel_token=None) -> None:
        self._team_lead = team_lead
        self._task_id = task_id
        self._cancel_token = cancel_token

    @property
    def cancel_token(self):
        return self._cancel_token

    @cancel_token.setter
    def cancel_token(self, token):
        self._cancel_token = token

    async def on_event(self, event: AgentEvent) -> None:
        kind = event.kind
        if kind == "specialist_tool":
            tool_name = (
                event.metadata.get("tool", event.detail)
                if event.metadata else event.detail
            )
            try:
                self._team_lead.update_step(self._task_id, tool_name)
            except Exception:
                _null_logger.debug(
                    "update_step failed for %s", self._task_id, exc_info=True,
                )
            return
        if kind == "phase":
            phase = (
                event.metadata.get("phase", event.detail)
                if event.metadata else event.detail
            )
            try:
                self._team_lead.update_phase(self._task_id, phase)
            except Exception:
                _null_logger.debug(
                    "update_phase failed for %s", self._task_id, exc_info=True,
                )
            return
        # All other events (token, tool_call, tool_result, specialist_*,
        # thinking_delta, done, …) are intentionally dropped — they would
        # otherwise leak into the foreground chat stream.
        _null_logger.debug(
            "[callbacks] SilentSubagentCallback dropped event kind=%s task=%s",
            kind, self._task_id,
        )

    async def on_approval_request(self, skill_name: str, arguments: dict) -> bool:
        _null_logger.debug(
            "Subagent %s requested approval for %s — auto-denied (background lane)",
            self._task_id, skill_name,
        )
        return False

    async def on_help_request(self, context: str, needs_browser: bool) -> str:
        return "skip"
