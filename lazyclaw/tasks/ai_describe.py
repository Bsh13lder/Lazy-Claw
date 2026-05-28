"""AI-generated task description.

One-shot worker-LLM helper that turns a task title + project category (and any
existing notes) into a 2–3 sentence explanation of what the task is about.
Cheap worker model (Gemma 4 E2B / Haiku fallback) — short prompt, ~1s.
"""

from __future__ import annotations

import asyncio
import logging

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# Hard cap so a runaway prompt never blocks the user's click.
_TIMEOUT_S = 6.0

_PROMPT = """Explain what the task below is about in 2–3 short sentences,
plain prose, no headers, no bullet lists. Be concrete and grounded — if the
context doesn't say something, don't invent it. Write in the same language as
the title.

Title: {title}
Project: {category}
Existing notes (may be empty): {existing}

Write only the explanation, nothing else."""


async def describe_task(
    config: Config,
    user_id: str,
    *,
    title: str,
    category: str | None,
    existing_description: str | None,
    timeout_s: float = _TIMEOUT_S,
) -> str | None:
    """Generate a short explanation of the task. Returns the text, or None on
    timeout / LLM failure. Never raises."""
    title = (title or "").strip()
    if not title:
        return None

    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter
    except Exception as exc:  # pragma: no cover — import-only failure
        logger.debug("ai_describe imports failed: %s", exc)
        return None

    prompt = _PROMPT.format(
        title=title[:300],
        category=(category or "(none)")[:120],
        existing=(existing_description or "(none)")[:600],
    )

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await asyncio.wait_for(
            eco.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You write clear, concise task explanations. Output prose only.",
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                user_id=user_id,
                role=ROLE_WORKER,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.debug("ai_describe worker LLM timed out after %.1fs", timeout_s)
        return None
    except Exception as exc:
        logger.debug("ai_describe worker LLM failed: %s", exc)
        return None

    text = (resp.content or "").strip()
    # Strip stray code fences just in case.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text or None
