"""Trace data models — frozen dataclasses for replay entries."""

from __future__ import annotations

from dataclasses import dataclass


# Entry types
LLM_CALL = "llm_call"
LLM_RESPONSE = "llm_response"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
TEAM_DELEGATION = "team_delegation"
TEAM_RESULT = "team_result"
CRITIC_REVIEW = "critic_review"
USER_MESSAGE = "user_message"
FINAL_RESPONSE = "final_response"

ALL_ENTRY_TYPES = frozenset({
    LLM_CALL, LLM_RESPONSE, TOOL_CALL, TOOL_RESULT,
    TEAM_DELEGATION, TEAM_RESULT, CRITIC_REVIEW,
    USER_MESSAGE, FINAL_RESPONSE,
})


@dataclass(frozen=True)
class TraceEntry:
    """Immutable trace entry."""

    id: str
    trace_session_id: str
    sequence: int
    entry_type: str
    content: str
    metadata: dict | None = None
    created_at: str = ""


@dataclass(frozen=True)
class TraceSession:
    """Summary of a trace session."""

    trace_session_id: str
    user_id: str
    entry_count: int
    started_at: str
    ended_at: str
    entry_types: tuple[str, ...] = ()
