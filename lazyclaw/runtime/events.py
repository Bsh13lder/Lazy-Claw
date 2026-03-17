"""Event kind constants and structured event data for agent observability.

Centralizes all event kinds used across agent, teams, and callbacks.
WorkSummary provides a structured summary of completed agent work.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Event kind constants
# ---------------------------------------------------------------------------

# Agent lifecycle
LLM_CALL = "llm_call"
TOKENS = "tokens"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
STREAM_DONE = "stream_done"
TOKEN = "token"
APPROVAL = "approval"
DONE = "done"
CANCELLED = "cancelled"

# Team lifecycle
TEAM_DELEGATE = "team_delegate"
TEAM_START = "team_start"
TEAM_MERGE = "team_merge"
SPECIALIST_START = "specialist_start"
SPECIALIST_THINKING = "specialist_thinking"
SPECIALIST_TOOL = "specialist_tool"
SPECIALIST_DONE = "specialist_done"

# Observability
WORK_SUMMARY = "work_summary"

ALL_EVENT_KINDS = frozenset({
    LLM_CALL, TOKENS, TOOL_CALL, TOOL_RESULT, STREAM_DONE, TOKEN,
    APPROVAL, DONE, CANCELLED,
    TEAM_DELEGATE, TEAM_START, TEAM_MERGE,
    SPECIALIST_START, SPECIALIST_THINKING, SPECIALIST_TOOL, SPECIALIST_DONE,
    WORK_SUMMARY,
})


# ---------------------------------------------------------------------------
# Work summary — structured data for post-task reporting
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkSummary:
    """Immutable summary of completed agent work.

    Passed in AgentEvent metadata under key "summary" for WORK_SUMMARY events.
    """

    duration_ms: int
    llm_calls: int
    tools_used: tuple[str, ...]
    specialists_used: tuple[str, ...]  # empty for direct mode
    total_tokens: int
    mode: str  # "direct" or "team"
    task_description: str  # first 100 chars of user message
    result_preview: str  # first 200 chars of response
