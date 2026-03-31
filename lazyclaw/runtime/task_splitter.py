"""Fast task splitter — detects compound messages and splits into sub-tasks.

Uses gpt-5-mini for a cheap, fast classification call (~50 tokens out).
Only triggers on messages that look compound ("and", "also", "then").
Single tasks pass through untouched (zero LLM cost).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from lazyclaw.llm.providers.base import LLMMessage

logger = logging.getLogger(__name__)


# ── Quick heuristic — skip LLM if clearly single task ────────────────

_COMPOUND_KEYWORDS = re.compile(
    r"\b(and also|and then|also|plus|additionally|then|after that|"
    r"meanwhile|at the same time|while you.re at it|"
    r"oh and|btw also|can you also)\b",
    re.IGNORECASE,
)

# "verb ... and verb" pattern catches: "clean gmail and check bitcoin"
# but NOT "search for cats and dogs" (noun and noun)
_COMMON_VERBS = (
    r"check|clean|delete|send|search|find|open|read|write|"
    r"look|get|show|tell|create|update|run|buy|book|schedule|"
    r"download|upload|summarize|compare|analyze|monitor|track|"
    r"cancel|remove|add|set|change|fix|debug|test|deploy"
)
_VERB_AND_VERB = re.compile(
    rf"\b({_COMMON_VERBS})\b.{{1,40}}\band\b.{{1,40}}\b({_COMMON_VERBS})\b",
    re.IGNORECASE,
)


def _looks_compound(message: str) -> bool:
    """Fast regex check — does this message LOOK like multiple tasks?"""
    if len(message) < 15:
        return False
    # Explicit compound keywords always trigger
    if _COMPOUND_KEYWORDS.search(message) is not None:
        return True
    # "verb ... and ... verb" pattern (e.g. "clean gmail and check bitcoin")
    return _VERB_AND_VERB.search(message) is not None


# ── LLM-based splitting ──────────────────────────────────────────────

_SPLIT_PROMPT = """You are a task splitter. Given a user message, determine if it contains multiple independent tasks.

Rules:
- Only split if there are truly SEPARATE tasks (different goals)
- "search for cats and dogs" = 1 task (same goal)
- "clean my gmail and check bitcoin price" = 2 tasks (different goals)
- "send email to John then check my calendar" = 2 tasks
- Single tasks = return as-is

Classify each sub-task lane:
- "foreground": needs browser, user interaction, or visual output
- "background": simple lookup, calculation, API call, no user visibility needed

Respond with ONLY valid JSON (no markdown):
{"tasks": [{"instruction": "...", "lane": "foreground|background", "name": "short_name"}]}

For single tasks: {"tasks": [{"instruction": "original message", "lane": "foreground", "name": "chat"}]}"""


@dataclass(frozen=True)
class SubTask:
    """A single sub-task extracted from a compound message."""

    instruction: str
    lane: str  # "foreground" | "background"
    name: str  # Short display name


async def split_tasks(
    eco_router,
    user_id: str,
    message: str,
    worker_model: str | None = None,
) -> list[SubTask]:
    """Split a compound message into sub-tasks.

    Uses eco_router brain role for cheap classification.
    Returns a list of SubTask. Single messages return [SubTask(original)].
    Never raises — returns single-task fallback on any error.
    """
    # Fast path: clearly single task
    if not _looks_compound(message):
        return [SubTask(instruction=message, lane="foreground", name="chat")]

    try:
        response = await eco_router.chat(
            messages=[
                LLMMessage(role="system", content=_SPLIT_PROMPT),
                LLMMessage(role="user", content=message),
            ],
            user_id=user_id,
            role="brain",
            max_tokens=200,
        )

        raw = response.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = json.loads(raw)
        tasks_data = parsed.get("tasks", [])

        if not tasks_data or len(tasks_data) < 2:
            # LLM says it's a single task
            return [SubTask(instruction=message, lane="foreground", name="chat")]

        result = []
        for t in tasks_data:
            result.append(SubTask(
                instruction=t.get("instruction", message),
                lane=t.get("lane", "foreground"),
                name=t.get("name", "task")[:20],
            ))

        logger.info(
            "Split '%s' into %d sub-tasks: %s",
            message[:40], len(result),
            [(s.name, s.lane) for s in result],
        )
        return result

    except Exception as exc:
        logger.debug("Task split failed (falling back to single): %s", exc)
        return [SubTask(instruction=message, lane="foreground", name="chat")]
