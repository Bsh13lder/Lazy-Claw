"""Weekly LazyBrain topic-rollup sweep.

Counts ``topic/{name}`` tag occurrences per user, picks the top-N most-used
topics, and synthesises a structured rollup note for each via the existing
``lazybrain.topic_rollup.topic_rollup`` function. Skips topics whose rollup
note was already produced within the cooldown window.

Stays self-contained — fire-and-forget, never raises, never blocks more
than a single brain LLM call per topic. Designed to be invoked from the
heartbeat daemon's daily tick.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain import events as lb_events
from lazyclaw.lazybrain import store as lb_store
from lazyclaw.lazybrain import topic_rollup as lb_topic_rollup

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# Run no more than this many rollups per user per sweep — bounds the brain
# LLM cost. Beyond ~10 topics the recall payoff drops sharply anyway.
MAX_TOPICS_PER_SWEEP = 10

# Skip topics with fewer than this many lessons. Below the threshold a
# rollup is more noise than signal — wait for more data.
MIN_LESSONS_PER_TOPIC = 3

# A topic's rollup is regenerated only once per cooldown window. Same
# rationale as the lesson dedup window: avoids burning brain LLM budget
# on near-identical syntheses.
ROLLUP_COOLDOWN_DAYS = 6


def _topic_from_tag(tag: str) -> str | None:
    if not tag or not tag.startswith("topic/"):
        return None
    suffix = tag.split("/", 1)[1].strip()
    return suffix or None


async def _topic_counts(config: "Config", user_id: str) -> list[tuple[str, int]]:
    """Return ``[(topic, count), ...]`` sorted by count desc.

    Reads the plaintext ``tags`` column directly — no decryption needed.
    """
    counter: Counter[str] = Counter()
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT tags FROM notes "
            "WHERE user_id = ? AND tags IS NOT NULL AND tags != ''",
            (user_id,),
        )
        for (raw,) in await rows.fetchall():
            for t in lb_store._load_tags(raw):
                topic = _topic_from_tag(t)
                if topic:
                    counter[topic] += 1
    return counter.most_common(MAX_TOPICS_PER_SWEEP)


async def _recent_rollup_exists(
    config: "Config", user_id: str, topic: str, cutoff_iso: str,
) -> bool:
    """True if a kind/rollup note for ``topic`` was created after ``cutoff_iso``.

    Uses the LIKE shortcut on the plaintext tags JSON so we never have to
    decrypt note bodies just to check freshness.
    """
    like_topic = f'%"topic/{topic}"%'
    like_kind = '%"kind/rollup"%'
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT 1 FROM notes "
            "WHERE user_id = ? AND created_at >= ? "
            "AND tags LIKE ? AND tags LIKE ? LIMIT 1",
            (user_id, cutoff_iso, like_topic, like_kind),
        )
        return (await rows.fetchone()) is not None


async def run_topic_rollup_sweep(
    config: "Config",
    user_id: str,
    *,
    cooldown_days: int = ROLLUP_COOLDOWN_DAYS,
    min_lessons: int = MIN_LESSONS_PER_TOPIC,
    max_topics: int = MAX_TOPICS_PER_SWEEP,
) -> dict:
    """Sweep one user. Returns ``{processed, skipped, errors}``.

    ``processed`` lists ``{topic, note_id, source_count}`` for newly-saved
    rollups. Skips topics on cooldown or with too few lessons. Never raises.
    """
    processed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    try:
        counts = await _topic_counts(config, user_id)
    except Exception:
        logger.exception("topic rollup: counting tags failed for user %s", user_id)
        return {"processed": [], "skipped": [], "errors": [{"stage": "count"}]}

    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    ).isoformat(timespec="seconds")

    for topic, count in counts[:max_topics]:
        if count < min_lessons:
            skipped.append({"topic": topic, "reason": "too_few", "count": count})
            continue
        try:
            if await _recent_rollup_exists(config, user_id, topic, cutoff_iso):
                skipped.append({"topic": topic, "reason": "cooldown"})
                continue
        except Exception:
            logger.debug(
                "topic rollup: cooldown check failed for %s", topic, exc_info=True,
            )

        try:
            data = await lb_topic_rollup.topic_rollup(config, user_id, topic)
        except Exception:
            logger.exception("topic rollup: brain call failed for %s", topic)
            errors.append({"topic": topic, "stage": "rollup"})
            continue

        body = (data.get("rollup") or "").strip()
        if not body or body.startswith("Rollup unavailable"):
            skipped.append({"topic": topic, "reason": "empty_or_llm_down"})
            continue

        try:
            note = await lb_store.save_note(
                config, user_id,
                content=body,
                title=f"Topic rollup: {topic}",
                tags=[
                    "kind/rollup",
                    f"topic/{topic}",
                    "auto",
                    "owner/agent",
                ],
                importance=5,
            )
        except Exception:
            logger.exception("topic rollup: save_note failed for %s", topic)
            errors.append({"topic": topic, "stage": "save"})
            continue

        try:
            lb_events.publish_note_saved(
                user_id, note["id"], note.get("title"),
                note.get("tags"), source="topic_rollup",
            )
        except Exception:
            logger.debug("topic rollup: publish failed", exc_info=True)

        processed.append({
            "topic": topic,
            "note_id": note["id"],
            "source_count": data.get("source_count", 0),
            "lesson_count": count,
        })

    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
    }


__all__ = ["run_topic_rollup_sweep", "MAX_TOPICS_PER_SWEEP",
           "MIN_LESSONS_PER_TOPIC", "ROLLUP_COOLDOWN_DAYS"]
