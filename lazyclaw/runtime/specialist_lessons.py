"""ADR-0005 Phase 6 — auto-improving specialists.

Thin adapter over the existing Skill-Lesson Learning Loop (ADR-0002,
``runtime/skill_lesson.py``). Each specialist accrues lessons during its
runs and recalls its own top lessons into context on the next run, so a
specialist gets better the more it is used — WITHOUT a parallel store.

Scoping: a specialist's lessons live in the SAME LazyBrain lesson store
as every other shape, tagged ``topic/<specialist_name>``. We reuse
``recall_skill_lessons`` verbatim (topic == specialist_name) instead of
reimplementing storage, embeddings, or outcome filtering.

Promotion: ``should_promote_lesson`` is the confidence gate for lifting a
recalled lesson from additive context into the specialist's *definition*
(a separate, manual-review step owned by the CRUD surface — this module
only decides eligibility). A lesson is promotable only when it is
``verified`` AND has survived enough successful replays.

Read-side functions are fire-and-forget: any failure degrades to an empty
block so a lesson-recall problem can never break a specialist run. Mirrors
the never-raises semantics of ``skill_lesson.recall_skill_lessons``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from lazyclaw.runtime.skill_lesson import (
    OUTCOME_VERIFIED,
    recall_skill_lessons,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# ── Promotion gate ───────────────────────────────────────────────────
#
# A recalled lesson is eligible for promotion into a specialist's
# *definition* only when it is verified AND has been successfully
# replayed at least this many times. ``replay_count`` is the confidence
# proxy: a shape the verification pump promoted (outcome=verified) and
# that has re-run cleanly several times is high-confidence. The bar is
# deliberately conservative — definition edits are sticky, so a low
# threshold could let a single lucky run rewrite a specialist.
PROMOTION_MIN_REPLAY_COUNT = 3

# Hard cap on lessons injected into a specialist's context per run. Keeps
# the block small — specialists already carry a system prompt + the task.
_RECALL_LIMIT_CAP = 5

_BLOCK_HEADER = "## Learned (from past runs)"
_BULLET_TEXT_CAP = 240


# ── Read side: recall this specialist's own lessons ──────────────────


def _base_topic(specialist_name: str) -> str:
    """`browser_specialist` → `browser`. Lesson writers never produce
    `<name>_specialist` topics, so the suffixed form can never match."""
    return specialist_name.removesuffix("_specialist")


async def recall_specialist_lessons(
    config: "Config",
    user_id: str,
    specialist_name: str,
    message: str,
    *,
    limit: int = 3,
) -> str:
    """Return a short markdown block of this specialist's top past lessons.

    Reuses ``recall_skill_lessons`` with ``topic=specialist_name`` so a
    specialist's accrued shapes feed back into its own context on the
    next run. The block is headed ``## Learned (from past runs)`` and
    carries one bullet per lesson (intent + action + outcome).

    Returns ``''`` when there are no lessons, when inputs are empty, or on
    ANY failure — recall must never break a specialist run.
    """
    if not specialist_name or not user_id:
        return ""

    k = max(1, min(_coerce_int(limit, default=3) or 3, _RECALL_LIMIT_CAP))
    try:
        lessons = await recall_skill_lessons(
            config,
            user_id,
            # Writers only ever produce BASE topics ("browser", "email",
            # skill names) — querying "browser_specialist" missed 11/11
            # in prod (2026-08-21 audit). Map to the base topic so
            # recall can actually hit.
            topic=_base_topic(specialist_name),
            intent=message or "",
            k=k,
        )
    except Exception:
        logger.debug(
            "[speclesson] recall_specialist_lessons failed specialist=%s",
            specialist_name,
            exc_info=True,
        )
        return ""

    logger.debug(
        "[speclesson] recall specialist=%s k=%d matched=%d",
        specialist_name, k, len(lessons),
    )
    if not lessons:
        return ""
    return _format_specialist_lessons(lessons)


def _format_specialist_lessons(lessons: list[dict]) -> str:
    """Render recalled lessons as a compact markdown bullet block.

    Empty string when nothing renders to a non-trivial bullet so callers
    can ``if block:`` cheaply.
    """
    bullets: list[str] = []
    for lesson in lessons:
        if not isinstance(lesson, Mapping):
            continue
        line = _lesson_line(lesson)
        if line:
            bullets.append(f"- {line}")
    if not bullets:
        return ""
    return _BLOCK_HEADER + "\n" + "\n".join(bullets)


def _lesson_line(lesson: Mapping[str, Any]) -> str:
    """One sanitized, single-line bullet for a recalled lesson."""
    intent = str(lesson.get("intent") or "").strip()
    action = str(lesson.get("action") or "").strip()
    outcome = str(lesson.get("outcome") or "").strip()

    label = intent or action or ""
    if not label:
        return ""
    line = label
    if action and action != label:
        line = f"{label} — `{action}`"
    if outcome:
        line = f"{line} [{outcome}]"

    line = _sanitize(line)
    # Collapse any newlines so one lesson stays one bullet.
    line = " ".join(line.split())
    if len(line) > _BULLET_TEXT_CAP:
        line = line[:_BULLET_TEXT_CAP] + "…"
    return line


def _sanitize(text: str) -> str:
    """Mangle any leaked sender-timestamp quote shapes in a lesson line.

    Lesson bodies for channel-reading specialists (upwork / whatsapp /
    email / instagram / telegram) can contain ``**Sender (HH:MM):**``
    fragments. Run them through the existing paraphrase machinery so the
    brain can't lift them verbatim as live channel quotes (the
    documented self-perpetuating-hallucination class). Defensive import →
    plain text if the sanitizer module is unavailable.
    """
    if not text:
        return ""
    try:
        from lazyclaw.lazybrain.paraphrase_sanitizer import (
            strip_sender_timestamp_patterns,
        )

        return strip_sender_timestamp_patterns(text)
    except Exception:
        # Sanitizer must never break recall — fall back to plain text.
        logger.debug(
            "[speclesson] _sanitize paraphrase strip failed, using raw line",
            exc_info=True,
        )
        return text


# ── Promotion gate: definition-promotion eligibility ─────────────────


def should_promote_lesson(lesson: Mapping[str, Any]) -> bool:
    """Confidence gate: is this lesson eligible for definition-promotion?

    Promotion lifts a lesson from additive context into a specialist's
    *definition*. That is a sticky edit, so the bar is conservative:

      1. ``outcome == 'verified'`` — survived the ADR-0002 verification
         pump (or a manual ``/confirm``), and
      2. ``replay_count >= PROMOTION_MIN_REPLAY_COUNT`` — re-run cleanly
         enough times to trust.

    Returns ``False`` on missing / malformed input rather than raising —
    the caller treats a non-promotable lesson and an unparseable one
    alike. Pure predicate; never raises.
    """
    if not isinstance(lesson, Mapping):
        return False
    outcome = str(lesson.get("outcome") or "").strip()
    if outcome != OUTCOME_VERIFIED:
        return False
    replay_count = _replay_count(lesson)
    eligible = replay_count >= PROMOTION_MIN_REPLAY_COUNT
    logger.debug(
        "[speclesson] should_promote outcome=%s replay_count=%d eligible=%s",
        outcome, replay_count, eligible,
    )
    return eligible


def _replay_count(lesson: Mapping[str, Any]) -> int:
    """Best-effort ``replay_count`` from a recall dict or note frontmatter.

    ``recall_skill_lessons`` doesn't surface ``replay_count`` inline, so
    fall back to parsing the note body's frontmatter when the recall dict
    didn't carry it. Returns 0 on anything unparseable. ``bool`` is
    rejected explicitly (it is an ``int`` subclass but never a count).
    """
    raw = lesson.get("replay_count")
    parsed = _coerce_int(raw, default=None)
    if parsed is not None:
        return max(0, parsed)

    body = lesson.get("body")
    if isinstance(body, str) and body:
        try:
            from lazyclaw.lazybrain.frontmatter import parse_frontmatter

            props, _body, _has = parse_frontmatter(body)
            val = _coerce_int(props.get("replay_count"), default=None)
            if val is not None:
                return max(0, val)
        except Exception:
            logger.debug(
                "[speclesson] _replay_count frontmatter parse failed",
                exc_info=True,
            )
            return 0
    return 0


def _coerce_int(value: Any, *, default: int | None) -> int | None:
    """Coerce ``value`` to ``int`` (rejecting ``bool``), else ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


__all__ = [
    "PROMOTION_MIN_REPLAY_COUNT",
    "recall_specialist_lessons",
    "should_promote_lesson",
]
