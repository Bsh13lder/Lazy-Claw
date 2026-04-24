"""LLM-backed structured extraction for complex task input.

When the regex-based ``nl_time.parse_full`` can't decode a user's phrase
(e.g. "set up dentist appointment for next month before the school trip"),
this module hands the text to the ECO worker model and asks for a strict
JSON draft. Worker role keeps it cheap and local when possible.

Contract: input is a free-text string. Output is the same shape the REST
layer returns from the regex fast path, so the UI doesn't branch.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from lazyclaw.config import Config
from lazyclaw.tasks.nl_time import parse_full as regex_parse_full

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You extract structured task fields from a user's free-text sentence.

Return ONLY a JSON object with these keys (use null when not mentioned):
  title         — string, a short actionable phrase. NEVER include the time.
  due_date      — "YYYY-MM-DD" or null.
  reminder_at   — full ISO-8601 datetime string (with timezone) or null.
  priority      — "urgent" | "high" | "medium" | "low" or null.
  category      — short lowercase word like "shopping", "health", "work" or null.
  tags          — array of short lowercase strings (no # prefix) or [].
  steps         — array of short action strings (sub-tasks) or [].

Rules:
  - If the user says "tomorrow", "in 2 hours", "next Monday" etc., you MUST
    resolve it against ``now`` (provided below) and output concrete dates.
  - Keep the title terse — strip time phrases, priority words, tags.
  - Output strictly valid JSON with no markdown fences, no commentary.
"""


def _extract_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of a possibly-messy LLM response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", stripped, flags=re.MULTILINE)
    # Greedy match to the last closing brace — LLMs sometimes append commentary.
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def ai_parse_task(config: Config, user_id: str, text: str) -> dict:
    """Run the ECO worker on the input and coerce the response into our shape.

    Falls back to the regex parser when the model is unavailable (no Ollama,
    API quota, etc.) so the UI keeps working even when ECO is down.
    """
    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    paid_router = LLMRouter(config)
    router = EcoRouter(config, paid_router)

    now_iso = datetime.now().astimezone().isoformat()
    user_msg = f"now: {now_iso}\ntimezone: Europe/Madrid\ninput: {text}"

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_msg),
    ]

    try:
        response = await router.chat(messages, user_id=user_id, role=ROLE_WORKER)
    except Exception:
        logger.info("ai_parse_task: worker unavailable, falling back to regex", exc_info=True)
        return regex_parse_full(text)

    content = getattr(response, "content", None) or getattr(response, "text", None) or ""
    parsed = _extract_json_object(content)
    if not parsed:
        logger.debug("ai_parse_task: unparseable response %r", content[:200])
        return regex_parse_full(text)

    # Normalize — the LLM sometimes returns strings for empty arrays, or uses
    # different field names. Coerce to our contract.
    def _as_list(v) -> list:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    return {
        "title": str(parsed.get("title") or "").strip(),
        "due_date": parsed.get("due_date") or None,
        "reminder_at": parsed.get("reminder_at") or None,
        "priority": parsed.get("priority") or None,
        "category": parsed.get("category") or None,
        "tags": _as_list(parsed.get("tags")),
        "steps": _as_list(parsed.get("steps")),
        "matched_time": None,
    }
