"""Message priority classifier — heuristic-based, no LLM call.

Classifies conversation messages by importance for compression:
- HIGH: tool results, decisions, code, important facts (keep verbatim)
- MEDIUM: questions, explanations, context (compress)
- LOW: greetings, filler, acknowledgments (drop)
"""

from __future__ import annotations

import re

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"

# Patterns for low-priority messages (greetings, filler)
_LOW_PATTERNS = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|got it|sure|yes|no|"
    r"good morning|good night|good evening|bye|goodbye|see you|"
    r"sounds good|perfect|great|awesome|nice|cool|understood|"
    r"alright|right|yep|yup|nope|hm+|ah+|oh+)\s*[!.?]*$",
    re.IGNORECASE,
)

# Patterns for high-priority content (decisions, code, important info)
_HIGH_PATTERNS = re.compile(
    r"(```|def |class |import |async |await |return |"
    r"decided|decision|conclusion|important|remember|"
    r"password|credential|api.?key|secret|token|"
    r"error|exception|traceback|bug|fix|"
    r"saved|created|deleted|updated|installed|deployed)",
    re.IGNORECASE,
)


def classify_message(
    role: str,
    content: str,
    tool_name: str | None = None,
    has_tool_calls: bool = False,
) -> str:
    """Classify a message's compression priority.

    Args:
        role: Message role (system, user, assistant, tool)
        content: Message text content
        tool_name: Tool call ID if this is a tool result
        has_tool_calls: Whether this assistant message has tool calls

    Returns:
        PRIORITY_HIGH, PRIORITY_MEDIUM, or PRIORITY_LOW
    """
    # System messages are always high priority
    if role == "system":
        return PRIORITY_HIGH

    # Tool results are always high priority (contain actionable data)
    if role == "tool" or tool_name:
        return PRIORITY_HIGH

    # Assistant messages with tool calls = high (shows what agent decided to do)
    if role == "assistant" and has_tool_calls:
        return PRIORITY_HIGH

    # Short messages matching greeting/filler patterns = low
    stripped = content.strip()
    if len(stripped) < 50 and _LOW_PATTERNS.match(stripped):
        return PRIORITY_LOW

    # Messages with high-priority content markers
    if _HIGH_PATTERNS.search(content):
        return PRIORITY_HIGH

    # Everything else = medium
    return PRIORITY_MEDIUM
