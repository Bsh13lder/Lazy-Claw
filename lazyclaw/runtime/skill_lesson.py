"""Cross-topic skill-outcome lessons — the system's own trainable memory.

Existing `lesson_extractor.py` captures lessons from *user corrections*
(types: `site` / `preference`). This module extends that machinery with a
third lesson source: **skill outcomes**. Every time a high-value skill
(n8n / instagram / email / whatsapp for now) succeeds, fails, or fixes
a prior failure, we write down the working (or failing) shape as a
LazyBrain note. Before the next similar call, `recall_skill_lessons`
pulls the best 3 and hands them to the LLM as few-shot exemplars.

Why this matters: MiniMax-M2.7 stalled for two days on "create Google
Sheet" because our tool interface hid n8n's node schema. Large models
like Haiku succeed because they memorized n8n's schema in training.
Smaller models (0.6B local workers, MiniMax, etc.) need the product
to remember instead. After one successful run by ANY model, the
shape is captured and replayable by every future model on every
future run — no validator upgrade required per node-type.

Storage is LazyBrain notes with a canonical tag set so the existing
PKM UI can browse / filter / graph these lessons alongside everything
else. Retrieval uses the existing embedding pipeline with graceful
substring fallback when Ollama is down.

Never raises — fire-and-forget semantics match `lesson_store.py`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# Topics we record lessons for. Keep narrow — only skills where a
# per-call schema is load-bearing AND where failure manifests as
# a cryptic server error that blocks progress.
LEARNING_TOPICS: frozenset[str] = frozenset({
    "n8n", "instagram", "email", "whatsapp",
})

# Keys that must never land in a lesson body, at any nesting depth.
# Mirrors the vault redaction pattern in the credential path.
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token", "id_token", "bearer",
    "api_key", "apikey", "api-key",
    "secret", "client_secret",
    "authorization", "auth",
    "cookie", "set-cookie",
    "private_key", "privatekey",
    "credentials",
})

_STRING_REDACTION_LIMIT = 200
_INTENT_SLUG_WORDS = 3


# ── Redaction ────────────────────────────────────────────────────────


def _redact(value: Any) -> Any:
    """Deep-copy ``value`` stripping sensitive keys and truncating strings.

    Stable on arbitrary JSON-ish trees (dict/list/scalar). Non-JSON types
    fall through to ``str(value)`` so the caller never sees a TypeError.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = "[redacted]"
                continue
            out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        if len(value) > _STRING_REDACTION_LIMIT:
            return value[:_STRING_REDACTION_LIMIT] + "…"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # Unknown type — coerce to string to keep JSON serialization safe.
    return str(value)[:_STRING_REDACTION_LIMIT]


def _intent_slug(intent: str) -> str:
    """First N meaningful words → dash-joined lowercase slug.

    Used as a `intent/<slug>` tag so agent-visible tag filters don't
    have to deal with per-word word-order noise."""
    words = re.findall(r"[A-Za-z0-9]+", (intent or "").lower())
    return "-".join(words[:_INTENT_SLUG_WORDS])[:60]


# ── Write side ───────────────────────────────────────────────────────


async def save_skill_lesson(
    config: "Config",
    user_id: str,
    *,
    topic: str,
    action: str,
    intent: str,
    params: dict | None = None,
    outcome: str = "success",
    error: str | None = None,
    fix_summary: str | None = None,
) -> str | None:
    """Persist a skill-outcome lesson to LazyBrain. Returns note id or None.

    Never raises. Silently no-ops when ``topic`` isn't in the learning set
    (unknown topics would pollute recall with garbage exemplars).
    """
    if topic not in LEARNING_TOPICS:
        logger.debug("skill_lesson: topic %r not in LEARNING_TOPICS, skipping", topic)
        return None
    if outcome not in {"success", "fail", "fix"}:
        logger.debug("skill_lesson: unknown outcome %r, skipping", outcome)
        return None

    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        clean_params = _redact(params) if params is not None else None
        try:
            params_block = (
                json.dumps(clean_params, ensure_ascii=False, indent=2)
                if clean_params is not None else ""
            )
        except Exception:
            params_block = str(clean_params)[:_STRING_REDACTION_LIMIT * 4]

        lines: list[str] = [
            f"**Topic:** {topic}",
            f"**Action:** {action}",
            f"**Intent:** {intent}",
            f"**Outcome:** {outcome}",
        ]
        if error:
            lines.append(f"**Error:** {_redact(error)}")
        if fix_summary:
            lines.append(f"**Fix:** {_redact(fix_summary)}")
        if params_block:
            lines.append("")
            lines.append("```json")
            lines.append(params_block)
            lines.append("```")
        body = "\n".join(lines)

        tags = [
            "lesson", "auto", "owner/agent",
            f"topic/{topic}",
            f"outcome/{outcome}",
            f"action/{action.split(':')[0]}",
        ]
        slug = _intent_slug(intent)
        if slug:
            tags.append(f"intent/{slug}")

        # Importance: success > fix > fail. Higher importance rises in
        # the personal_memory picker AND seeds stronger recall weight.
        importance = {"success": 6, "fix": 7, "fail": 3}.get(outcome, 5)

        note = await lb_store.save_note(
            config, user_id,
            content=body,
            title=f"Lesson ({topic}/{outcome}): {intent[:60]}",
            tags=tags,
            importance=importance,
        )
        try:
            lb_events.publish_note_saved(
                user_id, note["id"], note.get("title"),
                note.get("tags"), source="skill_lesson",
            )
        except Exception:
            logger.debug("skill_lesson: publish_note_saved failed", exc_info=True)

        logger.info(
            "skill_lesson saved: topic=%s action=%s outcome=%s intent=%s id=%s",
            topic, action, outcome, intent[:60], note["id"],
        )
        return note["id"]
    except Exception:
        logger.warning("skill_lesson save failed", exc_info=True)
        return None


# ── Read side ────────────────────────────────────────────────────────


async def recall_skill_lessons(
    config: "Config",
    user_id: str,
    *,
    topic: str,
    intent: str,
    k: int = 3,
    outcomes: tuple[str, ...] = ("success", "fix"),
) -> list[dict]:
    """Return up to ``k`` past lessons for ``topic`` matching ``intent``.

    Each element carries ``{title, intent, action, outcome, params, body}``.
    Empty list on no matches, on Ollama-down with no substring matches,
    on unknown topic, or on any error (never raises).
    """
    if topic not in LEARNING_TOPICS:
        return []

    try:
        from lazyclaw.lazybrain import embeddings as lb_emb

        query = f"topic:{topic} {intent}"
        hit = await lb_emb.semantic_search(config, user_id, query, k=max(1, k * 3))
        results = hit.get("results") or []
    except Exception:
        logger.debug("skill_lesson recall failed", exc_info=True)
        return []

    out: list[dict] = []
    wanted = set(outcomes)
    for note in results:
        tags = [str(t) for t in (note.get("tags") or [])]
        if f"topic/{topic}" not in tags:
            continue
        # Outcome filter — only success/fix exemplars by default.
        this_outcome = next(
            (t.split("/", 1)[1] for t in tags if t.startswith("outcome/")),
            None,
        )
        if this_outcome not in wanted:
            continue
        parsed = _parse_lesson_body(note.get("content") or "")
        out.append({
            "title": note.get("title") or "",
            "intent": parsed.get("intent", ""),
            "action": parsed.get("action", ""),
            "outcome": this_outcome or "",
            "params": parsed.get("params"),
            "body": note.get("content") or "",
            "note_id": note.get("id"),
            "score": note.get("_score"),
        })
        if len(out) >= k:
            break
    return out


# Matches the body layout written by `save_skill_lesson` above. Kept
# permissive — missing fields shouldn't break recall.
_FIELD_RE = re.compile(r"^\*\*([^:*]+):\*\*\s*(.*)$", re.MULTILINE)
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _parse_lesson_body(body: str) -> dict:
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(body or ""):
        fields[m.group(1).strip().lower()] = m.group(2).strip()
    params: Any = None
    j = _JSON_BLOCK_RE.search(body or "")
    if j:
        try:
            params = json.loads(j.group(1))
        except Exception:
            params = j.group(1)
    return {
        "topic": fields.get("topic", ""),
        "action": fields.get("action", ""),
        "intent": fields.get("intent", ""),
        "outcome": fields.get("outcome", ""),
        "error": fields.get("error", ""),
        "fix": fields.get("fix", ""),
        "params": params,
    }


def format_lessons_as_exemplars(lessons: list[dict]) -> str:
    """Markdown block ready to prepend to an LLM prompt.

    Compact on purpose — lessons for small models must not blow the
    context budget. ≤ 2 KB typical, caller enforces k."""
    if not lessons:
        return ""
    blocks: list[str] = ["## Known-good past shapes for similar tasks"]
    for i, lesson in enumerate(lessons, 1):
        blocks.append(
            f"\n### Example {i} — {lesson.get('intent', '(no intent)')} "
            f"[{lesson.get('outcome', '')}]"
        )
        if lesson.get("action"):
            blocks.append(f"Action: `{lesson['action']}`")
        params = lesson.get("params")
        if params is not None:
            try:
                pretty = json.dumps(params, ensure_ascii=False, indent=2)
            except Exception:
                pretty = str(params)
            blocks.append("Working parameters:")
            blocks.append("```json")
            blocks.append(pretty)
            blocks.append("```")
    blocks.append("\nReuse these shapes when the current request is analogous.")
    return "\n".join(blocks)
