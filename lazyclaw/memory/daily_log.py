"""Daily log summaries — auto-summarize sessions, encrypted at rest."""

from __future__ import annotations

import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def save_daily_log(
    config: Config,
    user_id: str,
    date: str,
    summary: str,
    key_events: str | None = None,
) -> str:
    """Upsert daily log (one per user per day). Returns log ID."""
    key = await get_user_dek(config, user_id)
    encrypted_summary = encrypt(summary, key)
    encrypted_events = encrypt(key_events, key) if key_events else None

    async with db_session(config) as db:
        # Check if log already exists for this date
        row = await db.execute(
            "SELECT id FROM daily_logs WHERE user_id = ? AND date = ?",
            (user_id, date),
        )
        existing = await row.fetchone()

        if existing:
            log_id = existing[0]
            await db.execute(
                "UPDATE daily_logs SET summary = ?, key_events = ? WHERE id = ?",
                (encrypted_summary, encrypted_events, log_id),
            )
        else:
            log_id = str(uuid4())
            await db.execute(
                "INSERT INTO daily_logs (id, user_id, date, summary, key_events) "
                "VALUES (?, ?, ?, ?, ?)",
                (log_id, user_id, date, encrypted_summary, encrypted_events),
            )
        await db.commit()

    logger.debug("Saved daily log for user %s date %s", user_id, date)

    # Mirror into LazyBrain as an agent-owned journal entry.
    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        body_parts = [summary]
        if key_events:
            body_parts.append(f"\n**Key events**\n{key_events}")
        note = await lb_store.save_note(
            config,
            user_id,
            content="\n".join(body_parts),
            title=f"Daily summary — {date}",
            tags=[
                "daily-log", "auto", "owner/agent",
                f"journal/{date}",
            ],
            importance=4,
        )
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="daily-log",
        )
    except Exception:
        logger.debug("lazybrain daily_log mirror failed", exc_info=True)

    return log_id


async def get_daily_log(config: Config, user_id: str, date: str) -> dict | None:
    """Get a specific daily log by date. Returns dict or None."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id, date, summary, key_events, created_at "
            "FROM daily_logs WHERE user_id = ? AND date = ?",
            (user_id, date),
        )
        result = await row.fetchone()

    if not result:
        return None

    return {
        "id": result[0],
        "date": result[1],
        "summary": decrypt_field(result[2], key),
        "key_events": decrypt_field(result[3], key),
        "created_at": result[4],
    }


async def list_daily_logs(config: Config, user_id: str, limit: int = 30) -> list[dict]:
    """List recent daily logs, newest first."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, date, summary, key_events, created_at "
            "FROM daily_logs WHERE user_id = ? ORDER BY date DESC LIMIT ?",
            (user_id, limit),
        )
        results = await rows.fetchall()

    logs = []
    for r in results:
        logs.append({
            "id": r[0],
            "date": r[1],
            "summary": decrypt_field(r[2], key),
            "key_events": decrypt_field(r[3], key),
            "created_at": r[4],
        })
    return logs


async def delete_daily_log(config: Config, user_id: str, date: str) -> bool:
    """Delete a daily log by date. Returns True if deleted."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM daily_logs WHERE user_id = ? AND date = ?",
            (user_id, date),
        )
        await db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Auto-summarization
# ---------------------------------------------------------------------------

async def generate_daily_summary(config: Config, user_id: str, date: str) -> str:
    """Summarize a day's conversations using the LLM. Stores result. Returns summary."""
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.llm.providers.base import LLMMessage

    key = await get_user_dek(config, user_id)

    # Fetch day's messages
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT role, content, created_at FROM agent_messages "
            "WHERE user_id = ? AND date(created_at) = ? ORDER BY created_at",
            (user_id, date),
        )
        results = await rows.fetchall()

    if not results:
        return "No conversations found for this date."

    # Decrypt and format messages
    conversation_lines = []
    for r in results:
        role = r[0]
        content = decrypt_field(r[1], key)
        conversation_lines.append(f"[{role}]: {content}")

    conversation_text = "\n".join(conversation_lines[:100])  # Cap at 100 messages

    # Summarize via LLM — use ECO router (ROLE_WORKER = cheap/local)
    summary_prompt = (
        "Summarize this day's conversations into a brief daily log. "
        "Include: key topics discussed, decisions made, tasks completed, and any important information. "
        "Keep it concise (2-4 paragraphs).\n\n"
        f"Date: {date}\n\n"
        f"Conversations:\n{conversation_text}"
    )

    messages = [
        LLMMessage(role="system", content="You are a helpful assistant that creates concise daily summaries."),
        LLMMessage(role="user", content=summary_prompt),
    ]

    # Use eco_router if available, fallback to direct router
    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        eco = EcoRouter(config, LLMRouter(config))
        response = await eco.chat(messages, user_id=user_id, role=ROLE_WORKER)
    except Exception:
        logger.warning("EcoRouter unavailable for daily summary, falling back to direct LLM", exc_info=True)
        router = LLMRouter(config)
        response = await router.chat(messages, model=config.worker_model, user_id=user_id)
    summary = response.content

    # Extract key events (first line or bullet points)
    key_events = summary.split("\n")[0] if summary else None

    await save_daily_log(config, user_id, date, summary, key_events)
    logger.info("Generated daily summary for user %s date %s", user_id, date)
    return summary


async def generate_weekly_summary(
    config: Config, user_id: str, week_start: str,
) -> str:
    """Compress 7 daily logs into one weekly summary. Uses fast model.

    Args:
        week_start: ISO date string for the Monday of the week (e.g. "2026-03-10")
    """
    from datetime import date as _date, timedelta

    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    # Load daily logs for this week
    all_logs = await list_daily_logs(config, user_id, limit=30)
    start = _date.fromisoformat(week_start)
    end = start + timedelta(days=7)
    week_logs = [
        l for l in all_logs
        if not l["date"].endswith("_week")
        and not l["date"].endswith("_month")
        and start.isoformat() <= l["date"] < end.isoformat()
    ]

    if len(week_logs) < 2:
        return ""  # Not enough data for a weekly summary

    # Combine daily summaries
    text = "\n\n".join(
        f"**{l['date']}:**\n{l['summary']}" for l in sorted(week_logs, key=lambda x: x["date"])
    )

    messages = [
        LLMMessage(
            role="system",
            content="Compress these daily logs into a brief weekly summary (1-2 paragraphs). "
                    "Keep key decisions, outcomes, tasks completed, and important context.",
        ),
        LLMMessage(role="user", content=f"Week of {week_start}:\n\n{text}"),
    ]

    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        eco = EcoRouter(config, LLMRouter(config))
        response = await eco.chat(messages, user_id=user_id, role=ROLE_WORKER)
    except Exception:
        logger.warning("EcoRouter unavailable for weekly summary, falling back to direct LLM", exc_info=True)
        router = LLMRouter(config)
        response = await router.chat(messages, model=config.worker_model, user_id=user_id)
    summary = response.content

    await save_daily_log(config, user_id, f"{week_start}_week", summary, "weekly")
    logger.info("Generated weekly summary for user %s week %s", user_id, week_start)
    return summary
