"""Universal post-skill auto-learning hook.

Wraps every dispatched tool call so the system records what happened,
without each skill needing to call ``save_skill_lesson`` explicitly. The
recorder is fire-and-forget — never blocks, never raises.

Three pieces:

1. ``topic_for_skill`` — derives a learning topic from skill name prefix
   or category metadata (e.g. ``browser_open`` → ``"browser"``).
2. ``_NOISE_DENYLIST`` — meta/utility skills we never record (search_tools,
   recall_memories, todo_write, etc.) because their outcomes carry no
   replayable shape AND would flood recall.
3. ``record_skill_outcome`` — the dispatcher entry point. Derives topic +
   intent + outcome, dedups against an in-memory cache, hands the rest to
   ``save_skill_lesson``.

Designed to be called from ``ToolExecutor.execute / execute_allowed /
execute_batch`` after every ``skill.execute()`` await.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ── Topic derivation ─────────────────────────────────────────────────

# Explicit prefix → topic map. Order matters: longer prefixes first so
# "lazybrain_topic_rollup" maps to "lazybrain", not to a "topic" topic.
_TOPIC_FROM_PREFIX: tuple[tuple[str, str], ...] = (
    ("browser_", "browser"),
    ("instagram_", "instagram"),
    ("whatsapp_", "whatsapp"),
    ("telegram_", "telegram"),
    ("lazybrain_", "lazybrain"),
    ("computer_", "computer"),
    ("vault_", "vault"),
    ("n8n_", "n8n"),
    ("email_", "email"),
    ("gmail_", "email"),
    ("imap_", "email"),
    ("task_", "tasks"),
    ("job_", "tasks"),
    ("exa_", "web"),
    ("serp_", "web"),
    ("google_", "web"),
    ("duckduckgo", "web"),
)

# Bare skill names (no prefix pattern) that still want a stable topic.
_TOPIC_FROM_NAME: dict[str, str] = {
    "browser": "browser",
    "web_search": "web",
    "search_web": "web",
    "search": "web",
}

# Skill names we MUST NEVER auto-record. These are dispatcher-internal
# utilities, meta-tools, or recursive paths that would flood the corpus
# or cause an infinite loop (e.g. recording a save_note creates a note
# whose save would record another note).
_NOISE_DENYLIST: frozenset[str] = frozenset({
    # Meta / dispatcher
    "search_tools", "todo_write", "delegate", "dispatch_subagents",
    "request_user_approval", "run_background", "cancel_task",
    # Memory + recall (recursive risk)
    "recall_memories", "save_memory", "recall_topic_lessons",
    # LazyBrain reads + writes (writes would loop; reads have no shape value)
    "lazybrain_save_note", "lazybrain_update_note", "lazybrain_delete_note",
    "lazybrain_search", "lazybrain_get_note", "lazybrain_list_notes",
    "lazybrain_get_backlinks", "lazybrain_semantic_search",
    "lazybrain_recent", "lazybrain_pinned",
    # Trivial utilities
    "get_time", "now", "current_time", "calculate", "calc",
    # Read-only filesystem inspection
    "read_file", "list_directory", "file_exists",
})


def topic_for_skill(skill: "BaseSkill") -> str | None:
    """Return the learning topic for ``skill``, or ``None`` to skip recording.

    Checks (in order): denylist → exact name map → prefix map → category.
    Returns ``None`` for denylisted skills, generic ``"general"`` category
    skills with no name match (lessons would be uselessly broad), and
    skills lacking a name attribute.
    """
    name = getattr(skill, "name", None)
    if not name or name in _NOISE_DENYLIST:
        return None

    if name in _TOPIC_FROM_NAME:
        return _TOPIC_FROM_NAME[name]

    for prefix, topic in _TOPIC_FROM_PREFIX:
        if name.startswith(prefix):
            return topic

    category = getattr(skill, "category", "general") or "general"
    if category and category != "general":
        return category

    # Fall back to skill name itself — better than dropping the lesson.
    return name


# ── Intent + outcome derivation ──────────────────────────────────────


_INTENT_LIMIT = 80


def synthesize_intent(skill: "BaseSkill", args: dict[str, Any]) -> str:
    """Build a human-shaped intent string from skill metadata + first arg.

    Callers may inject a richer intent via ``args["_intent"]`` (popped by
    ``record_skill_outcome`` before this is called). Without that, we use
    ``display_name`` plus the first scalar arg value as a memorable label.
    """
    label = (
        getattr(skill, "display_name", None)
        or getattr(skill, "name", None)
        or "skill"
    )
    hint = ""
    if args:
        for key, value in args.items():
            if key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)):
                snippet = str(value).strip()
                if snippet:
                    hint = snippet
                    break
    out = f"{label}: {hint}" if hint else str(label)
    return out[:_INTENT_LIMIT]


_APPROVAL_PREFIX = "APPROVAL_REQUIRED:"
_ERROR_PREFIXES = ("Error:", "Error ", "error:", "ERROR:")


def outcome_from_result(
    result: Any,
    exception: BaseException | None,
) -> tuple[str, str | None]:
    """Return ``(outcome, error_message)``.

    ``outcome`` is one of ``"success"`` or ``"fail"``. ``"fix"`` is reserved
    for explicit retry-after-failure recordings — auto-recording can't
    detect that without state, so we keep it binary here.
    """
    if exception is not None:
        return "fail", f"{type(exception).__name__}: {exception}"[:240]

    if result is None:
        return "success", None

    text = result if isinstance(result, str) else getattr(result, "text", "")
    if not isinstance(text, str):
        text = str(text)

    if text.startswith(_APPROVAL_PREFIX):
        # Pending approval is neither success nor failure — skip recording.
        return "skip", None
    if any(text.startswith(p) for p in _ERROR_PREFIXES):
        return "fail", text[:240]
    return "success", None


# ── Dedup cache ──────────────────────────────────────────────────────


_DEDUP_WINDOW_SECONDS = 60
_DEDUP_MAX_ENTRIES = 1000
_recent_dedup: dict[tuple[str, str, str], float] = {}


def _should_record(user_id: str, skill_name: str, outcome: str) -> bool:
    """Return True if the same (user, skill, outcome) wasn't recorded in
    the last ``_DEDUP_WINDOW_SECONDS``. Prunes opportunistically when the
    cache crosses ``_DEDUP_MAX_ENTRIES``.
    """
    key = (user_id, skill_name, outcome)
    now = time.monotonic()
    last = _recent_dedup.get(key)
    if last is not None and (now - last) < _DEDUP_WINDOW_SECONDS:
        return False
    _recent_dedup[key] = now
    if len(_recent_dedup) > _DEDUP_MAX_ENTRIES:
        cutoff = now - _DEDUP_WINDOW_SECONDS
        for k, ts in list(_recent_dedup.items()):
            if ts < cutoff:
                _recent_dedup.pop(k, None)
    return True


# ── Public entry point ───────────────────────────────────────────────


async def record_skill_outcome(
    config: "Config | None",
    user_id: str | None,
    skill: "BaseSkill | None",
    args: dict[str, Any] | None,
    result: Any,
    exception: BaseException | None = None,
    *,
    intent_override: str | None = None,
) -> None:
    """Fire-and-forget post-skill recorder. Never raises.

    Called by ``ToolExecutor`` after every skill execution. Filters by
    denylist, derives topic + intent + outcome, dedups, then delegates to
    ``save_skill_lesson``.
    """
    if config is None or not user_id or skill is None:
        return

    try:
        topic = topic_for_skill(skill)
        if not topic:
            return

        outcome, error_msg = outcome_from_result(result, exception)
        if outcome == "skip":
            return

        skill_name = getattr(skill, "name", "") or "unknown"
        if not _should_record(user_id, skill_name, outcome):
            return

        clean_args = dict(args or {})
        explicit_intent = clean_args.pop("_intent", None)
        intent = (
            intent_override
            or (explicit_intent if isinstance(explicit_intent, str) else None)
            or synthesize_intent(skill, clean_args)
        )

        from lazyclaw.runtime.skill_lesson import save_skill_lesson

        await save_skill_lesson(
            config, user_id,
            topic=topic,
            action=skill_name,
            intent=intent,
            params=clean_args or None,
            outcome=outcome,
            error=error_msg,
        )
    except Exception:
        logger.debug("record_skill_outcome failed", exc_info=True)


__all__ = [
    "topic_for_skill",
    "synthesize_intent",
    "outcome_from_result",
    "record_skill_outcome",
]
