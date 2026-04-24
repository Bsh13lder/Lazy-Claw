"""Structured action errors for the browser skill.

Replaces ad-hoc string errors scattered across browser_actions/ so the agent
can branch retry strategy on the error code rather than regex-matching prose.

Usage:

    return str(ActionError(
        code=ActionErrorCode.NOT_FOUND,
        message="Selector matched zero elements.",
        hint="Take a fresh snapshot — the page may have re-rendered.",
        retry_strategy="re_read",
    ))

The string form keeps the current human-readable shape (so the LLM prompt
contract doesn't break), while the structured metadata is available to the
web canvas and any future ToolResult consumer via ``to_tool_result_meta()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActionErrorCode(str, Enum):
    """Canonical error codes the browser skill can emit.

    Pick the most specific code. When none fit exactly, prefer the most
    actionable retry strategy.
    """

    NOT_FOUND = "not_found"                      # selector / ref matched 0 elements
    TIMEOUT = "timeout"                          # action exceeded its timeout budget
    OCCLUDED = "occluded"                        # element exists but is covered by another
    OFF_SCREEN = "off_screen"                    # element is outside viewport — scroll first
    STALE_SNAPSHOT = "stale_snapshot"            # ref-id from an older snapshot; re-read needed
    NAVIGATION_CHANGED = "navigation_changed"    # URL shifted mid-action
    FRAME_DETACHED = "frame_detached"            # iframe or tab closed while acting
    POLICY_DENIED = "policy_denied"              # checkpoint/approval gate refused
    DEPENDENCY_MISSING = "dependency_missing"    # binary or optional extra not installed


# Known retry strategies. Free-form string so callers can add new ones without
# touching this enum, but the canonical values keep the agent's retry logic
# predictable.
RETRY_WAIT = "wait"                              # transient — try again in a moment
RETRY_SCROLL = "scroll"                          # bring element into view first
RETRY_RE_READ = "re_read"                        # take a fresh snapshot, then retry
RETRY_GIVE_UP = "give_up"                        # not recoverable — stop retrying
RETRY_ESCALATE_TO_VISION = "escalate_to_vision"  # fall back to ask_vision
RETRY_REAUTH = "reauth"                          # login / re-open the site


@dataclass(frozen=True)
class ActionError:
    """Immutable structured error returned from a browser action handler."""

    code: ActionErrorCode
    message: str
    hint: str = ""
    retry_strategy: str = ""

    def __str__(self) -> str:
        parts = [f"[{self.code.value}] {self.message}"]
        if self.hint:
            parts.append(f"Hint: {self.hint}")
        if self.retry_strategy:
            parts.append(f"Retry: {self.retry_strategy}")
        return " ".join(parts)

    def to_tool_result_meta(self) -> dict[str, str]:
        """Metadata shape for future ToolResult / web canvas consumers."""
        return {
            "error_code": self.code.value,
            "retry_strategy": self.retry_strategy,
        }
