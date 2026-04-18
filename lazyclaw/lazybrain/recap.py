"""Morning & evening daily recap for LazyBrain's journal.

Morning (``build_morning_briefing``):
  - Reads yesterday's journal + open tasks + recent pinned notes.
  - Worker LLM summarises into a short [!tip] Morning Briefing callout.
  - Appended to today's journal if not already present.

Evening prompt (``build_evening_prompt``):
  - Returns a conversational prompt the channel layer can send via push.
  - The user's reply becomes tonight's reflection entry (append_journal).

Designed to run manually via a skill, or scheduled via heartbeat cron. The
output is always a string; nothing is silently persisted without the
caller's explicit enqueue.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from lazyclaw.config import Config
from lazyclaw.lazybrain import journal, store

logger = logging.getLogger(__name__)

_MORNING_MARKER = "> [!tip] Morning Briefing"

_PROMPT = """Compose a short morning briefing for the user.

Inputs:

### Yesterday's journal
{yesterday}

### Currently pinned notes
{pinned}

Write 3 concise sections, each 1–2 sentences, inside a single markdown
callout block. No greeting, no meta commentary. Cite pinned notes as
[[Note Title]] when relevant. Format exactly as:

> [!tip] Morning Briefing
> **Yesterday you** — …
> **Today's priorities** — …
> **Watch out for** — …
"""


def _iso_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iso_yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


async def build_morning_briefing(
    config: Config,
    user_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Build + append today's morning briefing. Returns status dict.

    ``force=False`` (default) skips if today's journal already contains the
    briefing marker — keeps the daemon idempotent.
    """
    today = _iso_today()
    yesterday_iso = _iso_yesterday()

    today_note = await journal.get_journal(config, user_id, today)
    if today_note and _MORNING_MARKER in (today_note.get("content") or "") and not force:
        return {"status": "skipped", "reason": "already present", "date": today}

    yesterday_note = await journal.get_journal(config, user_id, yesterday_iso)
    yesterday_body = (yesterday_note or {}).get("content") or "(no journal yesterday)"
    yesterday_body = yesterday_body[:1500]

    pinned = await store.list_notes(config, user_id, pinned_only=True, limit=6)
    pinned_parts: list[str] = []
    for n in pinned:
        t = n.get("title") or "(untitled)"
        snippet = (n.get("content") or "").strip().splitlines()[0][:140]
        pinned_parts.append(f"- [[{t}]] — {snippet}")
    pinned_block = "\n".join(pinned_parts) or "(none)"

    prompt = _PROMPT.format(yesterday=yesterday_body, pinned=pinned_block)

    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await eco.chat(
            messages=[
                LLMMessage(
                    role="system",
                    content="You compose concise personal daily briefings.",
                ),
                LLMMessage(role="user", content=prompt),
            ],
            user_id=user_id,
            role=ROLE_WORKER,
        )
    except Exception as exc:
        logger.warning("morning briefing LLM failed: %s", exc)
        return {"status": "error", "reason": f"llm: {exc}", "date": today}

    text = (resp.content or "").strip()
    if not text:
        return {"status": "error", "reason": "empty LLM response", "date": today}

    if _MORNING_MARKER not in text:
        text = f"{_MORNING_MARKER}\n> " + text.replace("\n", "\n> ")

    appended = await journal.append_journal(config, user_id, text, today)
    return {
        "status": "appended",
        "date": today,
        "note_id": appended.get("id"),
    }


def build_evening_prompt() -> str:
    """Gentle question to kick off evening reflection."""
    return (
        "Evening check-in — what's one thing you learned today, "
        "one decision you made, or one thing you're still thinking about? "
        "Reply here and I'll add it to your journal."
    )
