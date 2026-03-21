"""Lesson extraction from user corrections — detects and extracts compact lessons.

Uses gpt-5-mini for cheap extraction (~$0.0001/call). All functions are
fire-and-forget safe — they never raise exceptions to the caller.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.llm.providers.base import LLMMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lesson:
    """Immutable lesson extracted from a user correction."""

    content: str        # 1-2 line compact lesson
    lesson_type: str    # "site" | "preference"
    domain: str | None  # for site lessons (e.g. "groq.com")
    importance: int     # 1-10 for personal_memory ranking


# ── Correction detection (fast regex, no LLM) ───────────────────────

_CORRECTION_RE = re.compile(
    r"\b(no[,.\s!]|wrong|actually|not that|i meant|i said|"
    r"that's not|that is not|don't do|stop doing|instead of|"
    r"the right way|you should have|try again|ugh|"
    r"i already told you|i told you|not what i asked|"
    r"do it differently|that's wrong|incorrect)\b",
    re.IGNORECASE,
)


def is_correction(message: str) -> bool:
    """Fast regex check if a message looks like a user correction.

    No LLM call — just pattern matching. False positives are fine
    because extract_lesson will filter them out via LLM.
    """
    return bool(_CORRECTION_RE.search(message))


# ── Lesson extraction (LLM-powered, cheap) ──────────────────────────

_EXTRACT_PROMPT = """You are a lesson extractor. The user corrected their AI assistant.
Extract a compact 1-2 line lesson that the AI should remember for next time.

Recent conversation:
{context}

User's correction: {correction}
Current URL: {url}

Output ONLY valid JSON (no markdown, no explanation):
{{"content": "1-2 line lesson", "type": "site or preference", "domain": "hostname or null", "importance": 1-10}}

Rules:
- "site" if the lesson is about a specific website (navigation, selectors, OAuth flow, etc.)
- "preference" if about user's general preferences or how they want things done
- domain must be a hostname like "groq.com" (not a full URL), or null for preferences
- importance: 8-10 for critical corrections, 5-7 for useful tips, 1-4 for minor preferences
- Keep content SHORT (1-2 lines max)"""


async def extract_lesson(
    eco_router: EcoRouter,
    user_id: str,
    user_message: str,
    recent_messages: list[LLMMessage],
    current_url: str | None = None,
) -> Lesson | None:
    """Extract a lesson from a user correction via gpt-5-mini.

    Returns Lesson or None if extraction fails or no lesson found.
    Never raises — all errors caught and logged.
    """
    try:
        from lazyclaw.llm.providers.base import LLMMessage as Msg

        # Build compact context from recent messages
        context_lines = []
        for msg in recent_messages[-4:]:
            role = msg.role.upper()
            content = (msg.content or "")[:200]
            context_lines.append(f"{role}: {content}")
        context = "\n".join(context_lines)

        url_str = current_url or "none"
        prompt = _EXTRACT_PROMPT.format(
            context=context, correction=user_message[:300], url=url_str,
        )

        messages = [
            Msg(role="system", content="You extract lessons from corrections. Output JSON only."),
            Msg(role="user", content=prompt),
        ]

        # Use eco_router with force_free for cost efficiency
        response = await eco_router.chat(messages, user_id=user_id)

        if not response or not response.content:
            return None

        # Parse JSON response
        raw = response.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)

        content = data.get("content", "").strip()
        if not content:
            return None

        lesson_type = data.get("type", "preference")
        if lesson_type not in ("site", "preference"):
            lesson_type = "preference"

        domain = data.get("domain")
        if domain and lesson_type == "site":
            # Normalize domain
            domain = domain.lower().strip()
            if domain.startswith("http"):
                domain = urlparse(domain).netloc or domain
        elif lesson_type == "site" and current_url:
            domain = urlparse(current_url).netloc

        importance = min(10, max(1, int(data.get("importance", 5))))

        lesson = Lesson(
            content=content[:200],  # Cap length
            lesson_type=lesson_type,
            domain=domain if lesson_type == "site" else None,
            importance=importance,
        )
        logger.info(
            "Extracted lesson: [%s] %s (domain=%s, importance=%d)",
            lesson.lesson_type, lesson.content[:60], lesson.domain, lesson.importance,
        )
        return lesson

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.debug("Lesson extraction parse error: %s", e)
        return None
    except Exception as e:
        logger.warning("Lesson extraction failed: %s", e)
        return None
