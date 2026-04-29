"""Collapse the pre-2026-04-29 lesson flood into single-card shapes.

For every user, group notes carrying both ``lesson`` AND ``auto`` tags by
``(topic, action, intent_slug)`` parsed from existing ``topic/*``,
``action/*``, ``intent/*`` tags. Within each bucket pick a survivor
(highest-importance success first, fallback most-recent), rewrite its
frontmatter as a Skill-shape card with ``replay_count = bucket_size``,
append a "Run history" section, and tag the rest ``kind/legacy +
outcome/superseded`` (their ``lesson`` tag is stripped so they vanish
from the FilterBar lesson chip).

Read-only by default. ``--apply`` flips to write mode. ``--user-id <id>``
narrows to a single user. ``--limit-per-bucket N`` caps run-history rows.

Run from repo root:

    .venv/bin/python -m scripts.migrate_lessons_v2          # dry-run
    .venv/bin/python -m scripts.migrate_lessons_v2 --apply  # write

Embeddings (note_embeddings rows) are intentionally NOT touched here —
the survivor's embedding refreshes on first body-touch via
``store._schedule_post_save_hooks``. Superseded notes' embeddings stay
queryable but are filtered out at the recall layer by the ``kind/shape``
+ ``outcome/verified`` requirement (skill_lesson.recall_skill_lessons).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from lazyclaw.config import Config, load_config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


_RUN_LOG_HEADER = "## Run log"
_LEGACY_TAG = "kind/legacy"
_SUPERSEDED_TAG = "outcome/superseded"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_pair(tags: list[str], prefix: str) -> str:
    for t in tags:
        if t.startswith(prefix):
            return t[len(prefix):]
    return ""


def _bucket_key(tags: list[str]) -> tuple[str, str, str] | None:
    """Build the ``(topic, action, intent_slug)`` key from tags. Notes
    without a topic tag are unbucketable — they get skipped, not collapsed.
    """
    topic = _extract_pair(tags, "topic/")
    if not topic:
        return None
    action = _extract_pair(tags, "action/") or "unknown"
    intent_slug = _extract_pair(tags, "intent/") or "no-intent"
    return (topic, action, intent_slug)


async def _list_lesson_notes(
    config: Config, user_id: str | None
) -> list[dict]:
    """Return raw rows tagged ``lesson`` + ``auto``. Tags come back as the
    JSON string; the caller decodes."""
    where = ["tags LIKE '%\"lesson\"%'", "tags LIKE '%\"auto\"%'"]
    params: list = []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    sql = (
        "SELECT id, user_id, tags, importance, created_at, updated_at "
        "FROM notes WHERE " + " AND ".join(where) + " ORDER BY created_at ASC"
    )
    async with db_session(config) as db:
        rows = await db.execute(sql, params)
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "tags_raw": r[2],
                "importance": r[3] or 5,
                "created_at": r[4],
                "updated_at": r[5],
            }
            for r in await rows.fetchall()
        ]


def _decode_tags(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    try:
        import json
        parsed = json.loads(raw)
        return [str(t) for t in parsed if t]
    except Exception:
        return []


async def _bucket_user_notes(
    notes: Iterable[dict],
) -> dict[tuple[str, str, str, str], list[dict]]:
    """Group by ``(user_id, topic, action, intent_slug)``."""
    buckets: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for n in notes:
        tags = _decode_tags(n.get("tags_raw"))
        n["tags"] = tags
        key = _bucket_key(tags)
        if not key:
            continue
        topic, action, intent = key
        buckets[(n["user_id"], topic, action, intent)].append(n)
    return buckets


def _pick_survivor(bucket: list[dict]) -> dict:
    """Highest-importance success first; fallback most-recent."""
    def score(n):
        tags = n.get("tags") or []
        is_success = any(t == "outcome/success" for t in tags)
        return (
            int(is_success),
            int(n.get("importance") or 0),
            n.get("created_at") or "",
        )
    return max(bucket, key=score)


async def _rewrite_survivor(
    config: Config,
    survivor: dict,
    bucket: list[dict],
    *,
    apply: bool,
    limit_per_bucket: int,
) -> None:
    """Tag the survivor as ``kind/shape outcome/verified`` and overwrite
    its frontmatter with replay_count + first/last_used_at + a capped
    Run-history section."""
    from lazyclaw.lazybrain import store as lb_store
    from lazyclaw.lazybrain.frontmatter import (
        merge_frontmatter,
        parse_frontmatter,
    )

    user_id = survivor["user_id"]
    note = await lb_store.get_note(config, user_id, survivor["id"])
    if not note:
        return

    content = note.get("content") or ""
    existing_props, body, _ = parse_frontmatter(content)
    topic = _extract_pair(survivor["tags"], "topic/")
    action = _extract_pair(survivor["tags"], "action/") or "unknown"
    intent = _extract_pair(survivor["tags"], "intent/") or "no-intent"

    sorted_bucket = sorted(bucket, key=lambda n: n.get("created_at") or "")
    first_used = sorted_bucket[0].get("created_at") or _now_iso()
    last_used = sorted_bucket[-1].get("created_at") or _now_iso()

    # Build a compact run-history block from the bucket.
    history_lines: list[str] = [_RUN_LOG_HEADER]
    for n in sorted_bucket[-limit_per_bucket:]:
        created = (n.get("created_at") or "")[:19]
        outcome = _extract_pair(n.get("tags") or [], "outcome/") or "?"
        history_lines.append(f"- {created} {outcome}")
    history_block = "\n".join(history_lines)

    # Strip any pre-existing run log so we don't compound duplicates.
    if _RUN_LOG_HEADER in body:
        body = body.split(_RUN_LOG_HEADER, 1)[0].rstrip()
    new_body = body.rstrip() + "\n\n" + history_block + "\n"

    # Replace the lesson tags with the new shape vocabulary.
    new_tags = [
        t for t in survivor["tags"]
        if not t.startswith("outcome/")
    ]
    if "kind/shape" not in new_tags:
        new_tags.append("kind/shape")
    new_tags.append("outcome/verified")

    fm_updates = {
        "kind": "shape",
        "topic": topic,
        "action": action,
        "intent": existing_props.get("intent") or intent,
        "outcome": "verified",
        "replay_count": len(bucket),
        "first_used_at": first_used,
        "last_used_at": last_used,
        "migrated_from_lesson_v1": True,
    }

    logger.info(
        "[%s] survivor=%s topic=%s action=%s replay=%d",
        "APPLY" if apply else "DRY",
        survivor["id"], topic, action, len(bucket),
    )
    if not apply:
        return

    await lb_store.update_note(
        config, user_id, survivor["id"],
        content=new_body,
        tags=new_tags,
        importance=7,
        frontmatter_updates=fm_updates,
    )


async def _supersede_others(
    config: Config,
    survivor_id: str,
    bucket: list[dict],
    *,
    apply: bool,
) -> int:
    """Tag every non-survivor as ``kind/legacy outcome/superseded``,
    strip the legacy ``lesson`` tag so it falls off the FilterBar lesson
    chip. Returns rows touched.
    """
    from lazyclaw.lazybrain import store as lb_store

    touched = 0
    for n in bucket:
        if n["id"] == survivor_id:
            continue
        tags = list(n["tags"])
        new_tags = [t for t in tags if t not in ("lesson",)]
        # Replace any existing outcome with superseded.
        new_tags = [t for t in new_tags if not t.startswith("outcome/")]
        for marker in (_LEGACY_TAG, _SUPERSEDED_TAG):
            if marker not in new_tags:
                new_tags.append(marker)
        if not apply:
            touched += 1
            continue
        await lb_store.update_note(
            config, n["user_id"], n["id"],
            tags=new_tags,
            importance=1,
            frontmatter_updates={
                "outcome": "superseded",
                "kind": "legacy",
                "superseded_at": _now_iso(),
            },
        )
        touched += 1
    return touched


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write changes. Without this, run dry.")
    parser.add_argument("--user-id", default=None,
                        help="Limit to a single user (default: all).")
    parser.add_argument("--limit-per-bucket", type=int, default=50,
                        help="Cap on Run-history lines per survivor.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config()
    notes = await _list_lesson_notes(config, args.user_id)
    logger.info("Loaded %d candidate notes (lesson + auto).", len(notes))

    buckets = await _bucket_user_notes(notes)
    logger.info("Bucketed into %d (user, topic, action, intent) groups.",
                len(buckets))

    promoted = 0
    superseded = 0
    for key, bucket in buckets.items():
        if not bucket:
            continue
        survivor = _pick_survivor(bucket)
        await _rewrite_survivor(
            config, survivor, bucket,
            apply=args.apply, limit_per_bucket=args.limit_per_bucket,
        )
        promoted += 1
        n = await _supersede_others(
            config, survivor["id"], bucket, apply=args.apply,
        )
        superseded += n

    logger.info(
        "Done — survivors: %d, superseded: %d (apply=%s)",
        promoted, superseded, args.apply,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
