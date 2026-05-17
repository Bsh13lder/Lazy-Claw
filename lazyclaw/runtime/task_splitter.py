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


_FALSE_COMPOUND = re.compile(
    r"\b(and\s+(?:tell|show|let|give|send|report|explain|describe)\s+me)\b",
    re.IGNORECASE,
)

# "Save X's contact: <phone/email>" is always ONE task UNLESS the user also
# explicitly asks to message/send something. The number / email is contact
# data — not a "send to that number" intent. Catches the 2026-05-16 Gerardo
# false split, but stays out of the way when the user says "...also send X".
# Matches: "save gerardo's contact ...", "remember this contact ...", etc.
_SAVE_CONTACT_RE = re.compile(
    r"\b(save|store|remember|add|keep)\b[^\n]{0,40}\bcontacts?\b",
    re.IGNORECASE,
)
# Explicit messaging verbs that mean "also do a send" — if any of these
# appear, we let the splitter LLM decide. Tight list to avoid false hits.
_EXPLICIT_SEND_RE = re.compile(
    r"\b("
    r"send|message|text|dm|whatsapp\s+(?:him|her|them)|"
    r"email\s+(?:him|her|them)|ping|forward|notify|tell\s+(?:him|her|them)|"
    r"share\s+with\s+(?:him|her|them)|let\s+(?:him|her|them)\s+know"
    r")\b",
    re.IGNORECASE,
)

# Messages that OPEN with a single-intent verb ("scrape …", "find …",
# "list …", "search …") are almost never multi-task — even if they happen
# to contain "and". Skips a 2-3 s LLM round-trip on a common class of
# single tasks.
_SINGLE_INTENT_OPENER = re.compile(
    r"^\s*(?:ok\s+|please\s+|can\s+you\s+|could\s+you\s+|pls\s+|plz\s+|"
    r"first\s+|now\s+|your\s+task\s*[:,-]?\s*|i\s+want\s+(?:you\s+)?(?:to\s+)?)*"
    r"(scrape|scrap|scraping|crawl|find|list|search|look\s+up|fetch|"
    r"pull|grab|get|gather|collect|enumerate|summarize|summarise)\b",
    re.IGNORECASE,
)

# "Scrape N things" / "find all X" / "for each of Y" — multi-target work that
# belongs in the background lane (and ideally dispatched via subagents), not
# pinned to a foreground browser loop. Shared with agent.py to bias the plan
# prompt toward dispatch_subagents / run_background when these fire.
_ENUMERATION_RE = re.compile(
    r"\b("
    r"scrape|scrap|scraping|crawl|crawling|enumerate|"
    r"list\s+all|find\s+all|pull\s+all|get\s+all|"
    r"for\s+each\s+of|every\s+\w+|all\s+the\s+\w+|"
    r"bulk|mass|batch|harvest|"
    r"gather\s+(?:all|every|a\s+list)|"
    r"collect\s+(?:all|every|a\s+list)|"
    r"pull\s+(?:all|every|a\s+list)|"
    # Sheet/list enrichment patterns — multi-row work that takes >5 min
    # synchronously and should never block the foreground chat lane.
    # Catches "fill in missing emails", "populate the spreadsheet",
    # "enrich this list", "find emails for the salons", "complete the
    # missing data", "for each row", "go through the sheet".
    # Added 2026-04-29 after a Valencia salons run blocked chat for 15min+.
    r"fill\s+(?:in|out|the)\s+|"
    r"populate\s+(?:the\s+)?(?:list|sheet|spreadsheet|table|column|file|csv|rows?)|"
    r"enrich\s+(?:this|the|a)\s+(?:list|sheet|spreadsheet|table|data|column|file|csv)|"
    r"missing\s+(?:e-?mails?|addresses?|contacts?|phone\s+numbers?|info|infos?|data|values?|cells?|fields?|rows?|entries|details?)|"
    r"for\s+each\s+(?:row|item|entry|cell|record|line|salon|company|business|customer|client|contact|product|listing|lead)|"
    r"per\s+(?:row|item|entry|salon|company|business|customer|client|contact|product|listing|record|line)s?|"
    r"complete\s+(?:the\s+)?(?:list|sheet|spreadsheet|table|column|missing|rows?|entries)|"
    r"go\s+through\s+(?:the\s+)?(?:list|sheet|spreadsheet|table|rows?)|"
    # "find at least N X" / "find N emails" — numeric volume signals
    r"find\s+(?:at\s+least\s+)?\d+\s+\w+"
    r")\b",
    re.IGNORECASE,
)


def _looks_enumerative(message: str) -> bool:
    """Multi-target work that should run background / via dispatch_subagents."""
    if len(message) < 8:
        return False
    return _ENUMERATION_RE.search(message) is not None


def _looks_compound(message: str) -> bool:
    """Fast regex check — does this message LOOK like multiple tasks?"""
    if len(message) < 15:
        return False
    # Exclude "verb X and tell me" — that's a single task with reporting suffix
    if _FALSE_COMPOUND.search(message) is not None:
        return False
    # "Save contact: <data>" with NO explicit send verb — always a single
    # save task. The phone/email is contact data, not a send target.
    # If the user did add "also send / message him", let the LLM split.
    if (
        _SAVE_CONTACT_RE.search(message) is not None
        and _EXPLICIT_SEND_RE.search(message) is None
    ):
        return False
    # Single-intent openers ("scrape X", "find all Y", "search for Z") —
    # these are always a single task even when the object contains "and"
    # ("scrape restaurants and cafes"). Saves a 2-3 s LLM round-trip on
    # the most common false-compound case. Only applies when NO explicit
    # compound keyword is present ("and also", "then", "plus" still win).
    if (
        _SINGLE_INTENT_OPENER.match(message)
        and _COMPOUND_KEYWORDS.search(message) is None
    ):
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
- "Save X's contact: <phone/email>" or "remember this contact: ..." is ALWAYS 1 task.
  A phone number or email on its own line right after that intent is CONTACT DATA
  being saved — never a separate "send" instruction. Only split if the user
  ALSO writes an explicit send/message/text verb directed at the contact
  (e.g. "...also message him", "...and email her", "...send him my address").

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
        lane = "background" if _looks_enumerative(message) else "foreground"
        return [SubTask(instruction=message, lane=lane, name="chat")]

    try:
        parsed: dict | None = None
        # First attempt — normal prompt.
        for attempt in range(2):
            sys_prompt = _SPLIT_PROMPT
            if attempt == 1:
                sys_prompt = (
                    "Return ONLY a valid JSON object. No preamble, no markdown, "
                    "no trailing comma, no commentary. Close every string and "
                    "brace. Schema: {\"tasks\": [...]}\n\n" + _SPLIT_PROMPT
                )
            response = await eco_router.chat(
                messages=[
                    LLMMessage(role="system", content=sys_prompt),
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
            try:
                parsed = json.loads(raw)
                break
            except json.JSONDecodeError as exc:
                if attempt == 0:
                    logger.debug(
                        "Task split JSON parse failed (retrying once): %s", exc,
                    )
                    continue
                raise  # propagate to outer try/except → single-task fallback
        assert parsed is not None  # loop either broke with parsed set or raised
        tasks_data = parsed.get("tasks", [])

        if not tasks_data or len(tasks_data) < 2:
            # LLM says it's a single task
            lane = "background" if _looks_enumerative(message) else "foreground"
            return [SubTask(instruction=message, lane=lane, name="chat")]

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
        lane = "background" if _looks_enumerative(message) else "foreground"
        return [SubTask(instruction=message, lane=lane, name="chat")]
