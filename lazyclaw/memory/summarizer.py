"""LLM-powered conversation summarizer with priority guidance.

Takes a chunk of classified messages and produces a concise summary
that preserves high-priority items verbatim, compresses medium items,
and drops low-priority filler.
"""

from __future__ import annotations

import logging

from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.memory.classifier import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM

logger = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = """\
Summarize this conversation chunk for context continuity. Follow these rules:

1. **HIGH priority items** — Keep verbatim or near-verbatim. These include:
   tool results, code snippets, decisions, errors, credentials references.
2. **MEDIUM priority items** — Compress into brief summaries. Keep the key point.
3. **LOW priority items** — Drop entirely (greetings, filler, acknowledgments).

Output a concise summary (aim for 30-50% of original length) that preserves:
- All factual information and decisions
- Tool call results and their outcomes
- Code snippets and technical details
- What the user asked for and what was delivered

Format as a flowing narrative, not a list. Write in past tense.
Do NOT include any preamble like "Here is a summary" — just the summary itself.
"""


def _format_chunk_for_summary(
    messages: list[dict],
    classifications: list[str],
) -> str:
    """Format messages with priority tags for the LLM."""
    lines = []
    for msg, priority in zip(messages, classifications):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tag = f"[{priority.upper()}]"

        if role == "tool":
            lines.append(f"{tag} Tool result: {content[:500]}")
        elif role == "assistant" and msg.get("has_tool_calls"):
            lines.append(f"{tag} Assistant (called tools): {content[:300]}")
        else:
            lines.append(f"{tag} {role.capitalize()}: {content[:500]}")

    return "\n\n".join(lines)


async def summarize_chunk(
    eco_router: EcoRouter,
    user_id: str,
    messages: list[dict],
    classifications: list[str],
) -> str:
    """Summarize a conversation chunk using the LLM.

    Args:
        eco_router: LLM router (respects ECO mode)
        user_id: For routing and rate limiting
        messages: List of message dicts with role, content, etc.
        classifications: Parallel list of priority classifications

    Returns:
        Summary text string
    """
    formatted = _format_chunk_for_summary(messages, classifications)

    llm_messages = [
        LLMMessage(role="system", content=_SUMMARIZE_PROMPT),
        LLMMessage(role="user", content=formatted),
    ]

    response = await eco_router.chat(llm_messages, user_id=user_id)
    summary = response.content or ""

    logger.info(
        "Summarized %d messages (%d chars) into %d chars (%.0f%% reduction)",
        len(messages),
        len(formatted),
        len(summary),
        (1 - len(summary) / max(len(formatted), 1)) * 100,
    )

    return summary
