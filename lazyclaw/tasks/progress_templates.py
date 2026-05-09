"""Progress template CRUD — bundled function + learned skill.

Templates are the "what to ask and how often" part of a check-in pulse.
Schema is cloned from ``lazyclaw/browser/templates.py`` so the patterns
are familiar:

- Encrypted ``name`` + ``questions`` (free-form NL the user reads).
- Plaintext ``applies_to_category``, ``every`` (cron), ``buttons`` (label
  + structured callback action).
- Auto-save semantics via ``upsert_by_category`` — successful pulse
  patterns get promoted automatically; user-edited templates are
  preserved (we only fill empty fields on auto-save).
- Run/success metrics drive the lessons-v2 outcome promotion downstream.

The "learned skill" piece lives in ``runtime/skill_lesson.py`` — this
module just stores the template body. After 3 successful pulse cycles
on a category, the auto-save path bumps ``success_count`` and the
heartbeat hook calls ``save_skill_lesson(topic="task_progress",
action="pulse", intent=<category>, outcome="verified")``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


_ENCRYPTED_FIELDS = frozenset({"name", "questions"})

_COLUMNS = [
    "id", "user_id", "name", "applies_to_category", "every",
    "questions", "buttons",
    "auto_saved", "run_count", "success_count", "last_run_at",
    "version", "created_at",
]
_SELECT = ", ".join(_COLUMNS)


def _row_to_dict(row, key: bytes) -> dict:
    out: dict = {}
    for i, col in enumerate(_COLUMNS):
        v = row[i]
        if col in _ENCRYPTED_FIELDS:
            v = decrypt_field(v, key)
        if col == "questions" and v:
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                v = []
        if col == "buttons" and v:
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                v = []
        out[col] = v
    return out


def _normalize_questions(questions: list | None) -> list[dict]:
    """Coerce loose input into [{label, kind}] shape.

    Accepts plain strings (used as label, kind defaults to 'progress')
    or partial dicts. Out-of-range kinds collapse to 'progress'.
    """
    if not questions:
        return []
    valid_kinds = {"progress", "blocker", "eta", "note"}
    out: list[dict] = []
    for q in questions:
        if isinstance(q, str):
            label = q.strip()
            if label:
                out.append({"label": label[:200], "kind": "progress"})
            continue
        if not isinstance(q, dict):
            continue
        label = str(q.get("label", "")).strip()[:200]
        if not label:
            continue
        kind = str(q.get("kind") or "progress").strip().lower()
        if kind not in valid_kinds:
            kind = "progress"
        out.append({"label": label, "kind": kind})
    return out


def _normalize_buttons(buttons: list | None) -> list[dict]:
    """Coerce loose input into [{label, action}] shape.

    Action must follow the ``progress:<kind>`` callback prefix the
    Telegram handler routes on. Unknown actions are dropped silently.
    """
    if not buttons:
        return []
    valid_actions = {
        "progress:working", "progress:stuck", "progress:done",
        "progress:paused", "progress:blocked",
    }
    out: list[dict] = []
    for b in buttons:
        if not isinstance(b, dict):
            continue
        label = str(b.get("label") or "").strip()[:50]
        action = str(b.get("action") or "").strip().lower()
        if not label or action not in valid_actions:
            continue
        out.append({"label": label, "action": action})
    return out


def _validate_cron(every: str) -> str:
    """Sanity-check that ``every`` is a 5-field cron expression.

    Mirrors the validation done in heartbeat/orchestrator.create_job.
    Raises ValueError on bad input — better than letting an invalid
    expression make the daemon's is_due check fail every tick.
    """
    parts = (every or "").strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"every={every!r} must be a 5-field cron (got {len(parts)} fields)"
        )
    return " ".join(parts)


# ── CRUD ────────────────────────────────────────────────────────────────


async def create_template(
    config: Config,
    user_id: str,
    *,
    name: str,
    every: str,
    applies_to_category: str | None = None,
    questions: list | None = None,
    buttons: list | None = None,
    auto_saved: bool = False,
) -> dict:
    """Insert a new progress template. Returns the full decrypted dict."""
    every = _validate_cron(every)
    questions = _normalize_questions(questions)
    buttons = _normalize_buttons(buttons)

    key = await get_user_dek(config, user_id)
    template_id = str(uuid4())
    enc_name = encrypt(name, key)
    enc_questions = encrypt(json.dumps(questions), key) if questions else None
    buttons_json = json.dumps(buttons) if buttons else None
    created_at = datetime.now(timezone.utc).isoformat()

    placeholders = ", ".join(["?"] * len(_COLUMNS))
    async with db_session(config) as db:
        await db.execute(
            f"INSERT INTO progress_templates ({_SELECT}) VALUES ({placeholders})",
            (
                template_id, user_id, enc_name,
                applies_to_category, every,
                enc_questions, buttons_json,
                1 if auto_saved else 0, 0, 0, None,
                1, created_at,
            ),
        )
        await db.commit()

    return {
        "id": template_id, "user_id": user_id, "name": name,
        "applies_to_category": applies_to_category, "every": every,
        "questions": questions, "buttons": buttons,
        "auto_saved": 1 if auto_saved else 0,
        "run_count": 0, "success_count": 0, "last_run_at": None,
        "version": 1, "created_at": created_at,
    }


async def get_template(
    config: Config, user_id: str, template_id: str,
) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {_SELECT} FROM progress_templates "
            "WHERE id = ? AND user_id = ?",
            (template_id, user_id),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row, key) if row else None


async def list_templates(
    config: Config,
    user_id: str,
    applies_to_category: str | None = None,
) -> list[dict]:
    """List templates, optionally narrowed to a category match."""
    key = await get_user_dek(config, user_id)
    where = ["user_id = ?"]
    params: list = [user_id]
    if applies_to_category is not None:
        where.append("(applies_to_category = ? OR applies_to_category IS NULL)")
        params.append(applies_to_category)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {_SELECT} FROM progress_templates "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY success_count DESC, run_count DESC, created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r, key) for r in rows]


async def find_template_for_category(
    config: Config, user_id: str, category: str | None,
) -> dict | None:
    """Resolve which template best fits a task's category.

    Resolution order:
      1. Exact match on ``applies_to_category``.
      2. Substring match (template's category is a token in task's category).
      3. Generic fallback (``applies_to_category IS NULL``).
      4. None.

    Picked template is the one with the highest ``success_count`` at
    each tier so verified shapes win over auto-saved newcomers.
    """
    if category is not None:
        templates = await list_templates(
            config, user_id, applies_to_category=category,
        )
        # Exact match wins.
        for t in templates:
            if (t.get("applies_to_category") or "") == category:
                return t
        # Substring match — template category is a token in the task's
        # category. Useful for "writing-blog" task hitting a "writing"
        # template.
        for t in templates:
            tcat = (t.get("applies_to_category") or "").strip().lower()
            if tcat and tcat in (category or "").lower():
                return t

    # Generic fallback.
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {_SELECT} FROM progress_templates "
            "WHERE user_id = ? AND applies_to_category IS NULL "
            "ORDER BY success_count DESC, run_count DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    key = await get_user_dek(config, user_id)
    return _row_to_dict(row, key)


async def update_template(
    config: Config, user_id: str, template_id: str, **fields,
) -> bool:
    """Update fields on an existing template. Bumps version on user edits."""
    if not fields:
        return False

    if "every" in fields and fields["every"] is not None:
        fields["every"] = _validate_cron(fields["every"])

    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []

    for col, value in fields.items():
        if col == "name" and value is not None:
            value = encrypt(value, key)
        elif col == "questions":
            value = json.dumps(_normalize_questions(value)) if value else None
            if value:
                value = encrypt(value, key)
        elif col == "buttons":
            value = json.dumps(_normalize_buttons(value)) if value else None
        set_clauses.append(f"{col} = ?")
        params.append(value)

    # Bump version on every user edit so the auto-save path knows to
    # treat this as the canonical user-edited template.
    if "auto_saved" not in fields:
        set_clauses.append("version = version + 1")

    params.extend([template_id, user_id])
    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE progress_templates SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
    return result.rowcount > 0


async def delete_template(
    config: Config, user_id: str, template_id: str,
) -> bool:
    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM progress_templates WHERE id = ? AND user_id = ?",
            (template_id, user_id),
        )
        await db.commit()
    return result.rowcount > 0


async def bump_run_count(
    config: Config, user_id: str, template_id: str, *, success: bool = False,
) -> None:
    """Increment run_count (and success_count when applicable).

    Called by the daemon's pulse-fire path on every tick that pings
    the user, and by the response-handling path when the user actually
    replies (vs ignoring the pulse).
    """
    now = datetime.now(timezone.utc).isoformat()
    async with db_session(config) as db:
        if success:
            await db.execute(
                "UPDATE progress_templates "
                "SET run_count = run_count + 1, success_count = success_count + 1, "
                "last_run_at = ? "
                "WHERE id = ? AND user_id = ?",
                (now, template_id, user_id),
            )
        else:
            await db.execute(
                "UPDATE progress_templates "
                "SET run_count = run_count + 1, last_run_at = ? "
                "WHERE id = ? AND user_id = ?",
                (now, template_id, user_id),
            )
        await db.commit()


async def upsert_by_category(
    config: Config,
    user_id: str,
    *,
    applies_to_category: str | None,
    name: str,
    every: str,
    questions: list,
    buttons: list,
) -> dict:
    """Auto-save path — create a template if no match exists for the
    given category, else fill empty fields without clobbering user edits.

    Mirror of ``browser/templates.upsert_by_host``. The ``auto_saved``
    flag stays True only on first creation; user edits via
    ``update_template`` clear it implicitly (version bump).
    """
    existing = await find_template_for_category(config, user_id, applies_to_category)
    if existing is None:
        return await create_template(
            config, user_id,
            name=name, every=every,
            applies_to_category=applies_to_category,
            questions=questions, buttons=buttons,
            auto_saved=True,
        )

    # Fill-empty merge: only patch fields the user hasn't customized.
    patch: dict = {}
    if not existing.get("questions"):
        patch["questions"] = questions
    if not existing.get("buttons"):
        patch["buttons"] = buttons
    if patch:
        await update_template(config, user_id, existing["id"], **patch)
    return await get_template(config, user_id, existing["id"]) or existing


# ── Seed defaults — installed once per user on first registry hit ──────


_GENERIC_QUESTIONS = [
    {"label": "Where are you on this?", "kind": "progress"},
    {"label": "Any blocker?", "kind": "blocker"},
]
_CODING_QUESTIONS = [
    {"label": "What just compiled?", "kind": "progress"},
    {"label": "Stuck on something?", "kind": "blocker"},
]
_WRITING_QUESTIONS = [
    {"label": "Word count update?", "kind": "progress"},
    {"label": "Section in progress?", "kind": "progress"},
]
_DEFAULT_BUTTONS = [
    {"label": "🟡 Working", "action": "progress:working"},
    {"label": "🔴 Stuck", "action": "progress:stuck"},
    {"label": "✅ Done", "action": "progress:done"},
    {"label": "⏸️ Pause", "action": "progress:paused"},
]


async def ensure_default_templates(config: Config, user_id: str) -> None:
    """Seed the three bundled defaults the first time a user gets near
    progress tracking. Idempotent — calls upsert_by_category which
    silently skips if any template already covers the category."""
    seeds = [
        # Generic falls back when no category match.
        ("General pulse", None, "0 * * * *", _GENERIC_QUESTIONS),
        ("Coding pulse", "code", "*/30 * * * *", _CODING_QUESTIONS),
        ("Writing pulse", "writing", "*/45 * * * *", _WRITING_QUESTIONS),
    ]
    for name, category, every, questions in seeds:
        try:
            await upsert_by_category(
                config, user_id,
                applies_to_category=category,
                name=name,
                every=every,
                questions=questions,
                buttons=_DEFAULT_BUTTONS,
            )
        except Exception:
            logger.debug("seed template %r failed", name, exc_info=True)
