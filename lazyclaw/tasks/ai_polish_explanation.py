"""AI-polished MANUAL task explanation.

Companion to ``ai_describe`` (the AUTO mode). Here the user types their OWN
rough explanation of the task and the worker LLM rewrites it into clean prose —
keeping their meaning, inventing nothing. Cheap worker model (Gemma 4 E2B /
Haiku fallback), short prompt, hard timeout. Returns ``None`` on failure so the
caller can fall back to saving the user's raw text (never lose their input).
"""

from __future__ import annotations

import asyncio
import logging

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# Hard cap so a runaway prompt never blocks the user's click.
_TIMEOUT_S = 5.0

_PROMPT = """The user wrote a rough explanation of one of their tasks, in their
own words. Rewrite it as 2–3 clean sentences of plain prose — fix grammar and
clarity, keep their exact meaning, and DO NOT invent any fact, scope, date, or
detail they didn't write. No headers, no bullet lists. Write in the same
language the user used.

Task title: {title}
Project: {category}

User's rough explanation:
\"\"\"{raw}\"\"\"

Return ONLY the polished explanation, nothing else."""


async def polish_explanation(
    config: Config,
    user_id: str,
    *,
    raw_text: str,
    title: str,
    category: str | None,
    timeout_s: float = _TIMEOUT_S,
) -> str | None:
    """Polish the user's own explanation into clean prose. Returns the text, or
    None on empty input / timeout / LLM failure. Never raises."""
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None

    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter
    except Exception as exc:  # pragma: no cover — import-only failure
        logger.debug("ai_polish_explanation imports failed: %s", exc)
        return None

    prompt = _PROMPT.format(
        title=(title or "(none)")[:300],
        category=(category or "(none)")[:120],
        raw=raw_text[:1500],
    )

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await asyncio.wait_for(
            eco.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You polish task explanations. Keep the user's meaning, output prose only.",
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                user_id=user_id,
                role=ROLE_WORKER,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.debug("ai_polish_explanation worker LLM timed out after %.1fs", timeout_s)
        return None
    except Exception as exc:
        logger.debug("ai_polish_explanation worker LLM failed: %s", exc)
        return None

    text = (resp.content or "").strip()
    # Strip stray code fences / wrapping quotes just in case.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text or None
