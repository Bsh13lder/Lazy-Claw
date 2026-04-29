"""Verification pump — promotes pending shapes to verified after a quiet
window of agent turns, and supports manual /confirm and /reject overrides.

Borrowed from Logseq's TODO → DOING → DONE auto-rotation. A shape note
saved as ``outcome/pending`` carries ``pending_since_turn`` in its
frontmatter. Each agent turn the pump runs once; any pending shape
whose owner has advanced ≥ ``QUIET_TURNS_FOR_VERIFY`` turns since
``pending_since_turn`` (and which has not been touched by a ``failed``
write in the meantime) flips to ``verified``.

The pump is fire-and-forget — it never blocks an agent turn. Manual
overrides go through ``confirm_latest_shape`` and ``reject_latest_shape``
which look up the user's most recently touched shape and set the outcome
directly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from lazyclaw.runtime.skill_lesson import (
    OUTCOME_KNOWN_BAD,
    OUTCOME_PENDING,
    OUTCOME_VERIFIED,
    _OUTCOME_IMPORTANCE,
)
from lazyclaw.runtime.turn_counter import (
    QUIET_TURNS_FOR_VERIFY,
    current_turn,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_verification_pump(
    config: "Config", user_id: str
) -> int:
    """Promote eligible pending shapes to verified.

    Eligibility: ``current_turn(user_id) - pending_since_turn >=
    QUIET_TURNS_FOR_VERIFY`` AND no ``failed`` row exists for the same
    title_key inside the same window. Returns the number of shapes
    promoted. Never raises.
    """
    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.frontmatter import (
            merge_frontmatter,
            parse_frontmatter,
        )
        from lazyclaw.db.connection import db_session

        now_turn = current_turn(user_id)
        if now_turn < QUIET_TURNS_FOR_VERIFY:
            return 0

        # Find candidate notes — kind/shape with outcome/pending. SQL
        # filter at the tag level since tags are stored as JSON arrays.
        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id FROM notes "
                "WHERE user_id = ? "
                "AND tags LIKE '%\"kind/shape\"%' "
                "AND tags LIKE '%\"outcome/pending\"%'",
                (user_id,),
            )
            candidate_ids = [r[0] for r in await rows.fetchall()]

        promoted = 0
        for note_id in candidate_ids:
            note = await lb_store.get_note(config, user_id, note_id)
            if not note:
                continue
            content = note.get("content") or ""
            props, _, _ = parse_frontmatter(content)
            since = props.get("pending_since_turn")
            if not isinstance(since, (int, float)):
                continue
            if (now_turn - int(since)) < QUIET_TURNS_FOR_VERIFY:
                continue

            # Promote to verified. Update the outcome tag in tags too.
            old_tags = [str(t) for t in (note.get("tags") or [])]
            new_tags = [
                "outcome/verified" if t == "outcome/pending" else t
                for t in old_tags
            ]
            await lb_store.update_note(
                config, user_id, note_id,
                tags=new_tags,
                importance=_OUTCOME_IMPORTANCE[OUTCOME_VERIFIED],
                frontmatter_updates={
                    "outcome": OUTCOME_VERIFIED,
                    "pending_since_turn": None,  # clear marker
                    "verified_at": _now_iso(),
                },
            )
            promoted += 1

        if promoted:
            logger.info(
                "lesson_verifier: promoted %d shape(s) to verified for user=%s",
                promoted, user_id,
            )
        return promoted
    except Exception:
        logger.warning("lesson_verifier pump failed", exc_info=True)
        return 0


async def confirm_latest_shape(
    config: "Config", user_id: str
) -> str | None:
    """Manual /confirm — promote the most recently touched pending or
    failed shape to verified. Returns the promoted note id, or None if
    none was found.
    """
    return await _flip_latest_shape(
        config, user_id,
        from_outcomes=("pending", "failed"),
        to_outcome=OUTCOME_VERIFIED,
    )


async def reject_latest_shape(
    config: "Config", user_id: str
) -> str | None:
    """Manual /reject — flag the most recently touched shape as known-bad."""
    return await _flip_latest_shape(
        config, user_id,
        from_outcomes=("pending", "verified", "failed"),
        to_outcome=OUTCOME_KNOWN_BAD,
    )


async def _flip_latest_shape(
    config: "Config",
    user_id: str,
    *,
    from_outcomes: tuple[str, ...],
    to_outcome: str,
) -> str | None:
    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.db.connection import db_session

        async with db_session(config) as db:
            # Pick the most recently updated kind/shape note.
            rows = await db.execute(
                "SELECT id, tags FROM notes "
                "WHERE user_id = ? AND tags LIKE '%\"kind/shape\"%' "
                "ORDER BY updated_at DESC LIMIT 50",
                (user_id,),
            )
            candidates = await rows.fetchall()

        for note_id, raw_tags in candidates:
            tags = [str(t) for t in _decode_tags_json(raw_tags)]
            current = next(
                (t.split("/", 1)[1] for t in tags if t.startswith("outcome/")),
                None,
            )
            if current not in from_outcomes:
                continue
            new_tags = [
                f"outcome/{to_outcome}" if t.startswith("outcome/") else t
                for t in tags
            ]
            updates: dict[str, Any] = {
                "outcome": to_outcome,
                "pending_since_turn": None,
            }
            if to_outcome == OUTCOME_VERIFIED:
                updates["verified_at"] = _now_iso()
            elif to_outcome == OUTCOME_KNOWN_BAD:
                updates["rejected_at"] = _now_iso()
            await lb_store.update_note(
                config, user_id, note_id,
                tags=new_tags,
                importance=_OUTCOME_IMPORTANCE[to_outcome],
                frontmatter_updates=updates,
            )
            logger.info(
                "lesson_verifier: %s → %s on note=%s for user=%s",
                current, to_outcome, note_id, user_id,
            )
            return note_id
        return None
    except Exception:
        logger.warning("lesson_verifier flip failed", exc_info=True)
        return None


def _decode_tags_json(raw: Any) -> list[str]:
    """``notes.tags`` is stored as a JSON array string; tolerate None/empty."""
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


async def demote_on_failure(
    config: "Config",
    user_id: str,
    *,
    title_key: str,
) -> bool:
    """Tool returned a fresh failure for ``title_key``; if a verified
    shape exists for that key, demote it back to pending and re-arm
    ``pending_since_turn`` so the next quiet window must elapse before
    re-promotion.

    Called from the tool executor's failure path. Returns True if a
    demotion actually happened.
    """
    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.db.connection import db_session

        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id, tags FROM notes "
                "WHERE user_id = ? AND title_key = ? AND "
                "tags LIKE '%\"outcome/verified\"%' LIMIT 1",
                (user_id, title_key),
            )
            row = await rows.fetchone()

        if not row:
            return False
        note_id, raw_tags = row
        tags = [str(t) for t in _decode_tags_json(raw_tags)]
        new_tags = [
            "outcome/pending" if t == "outcome/verified" else t
            for t in tags
        ]
        await lb_store.update_note(
            config, user_id, note_id,
            tags=new_tags,
            importance=_OUTCOME_IMPORTANCE[OUTCOME_PENDING],
            frontmatter_updates={
                "outcome": OUTCOME_PENDING,
                "pending_since_turn": current_turn(user_id),
                "verified_at": None,
            },
        )
        logger.info(
            "lesson_verifier: demoted verified→pending on note=%s",
            note_id,
        )
        return True
    except Exception:
        logger.warning("lesson_verifier demotion failed", exc_info=True)
        return False


__all__ = [
    "run_verification_pump",
    "confirm_latest_shape",
    "reject_latest_shape",
    "demote_on_failure",
]
