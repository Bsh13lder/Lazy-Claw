"""Cross-topic skill-outcome lessons — the system's own trainable memory.

Borrowed PKM patterns: Anki spaced reinforcement (replay_count + last_used_at),
Logseq state machine (pending → verified, with auto-promotion via the
verification pump), Tana supertags (kind/shape, kind/fact, kind/known-bad),
Obsidian Tasks states ([ ] [/] [x] [-] [?]), Heptabase typed cards.

A "shape" is one Anki-style card per (topic, action, intent_slug) triple.
Re-execution updates the existing card's frontmatter (replay_count++,
last_used_at, run-log line) instead of writing a fresh note. The 60-second
in-memory dedup of the prior design is replaced by content-addressable
upsert keyed on `title_key`.

Outcome lifecycle:
  pending     — just executed, awaiting verification
  verified    — promoted by quiet-turn pump or /confirm
  failed      — tool returned error or exception
  superseded  — collapsed by migration (G) or replaced by newer card
  known-bad   — user said "don't do that" / /reject

Storage is LazyBrain notes with kind/shape + outcome/<state> tags so the
PKM UI can filter, hide ("Skills vault" toggle), and graph alongside
everything else. Retrieval uses the existing embedding pipeline with
graceful substring fallback when Ollama is down.

Never raises — fire-and-forget semantics match `lesson_store.py`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# ── Outcome state machine (Logseq + Obsidian Tasks) ──────────────────

OUTCOME_PENDING = "pending"
OUTCOME_VERIFIED = "verified"
OUTCOME_FAILED = "failed"
OUTCOME_SUPERSEDED = "superseded"
OUTCOME_KNOWN_BAD = "known-bad"

_OUTCOMES: frozenset[str] = frozenset({
    OUTCOME_PENDING,
    OUTCOME_VERIFIED,
    OUTCOME_FAILED,
    OUTCOME_SUPERSEDED,
    OUTCOME_KNOWN_BAD,
})

# Importance map: known-bad and verified rise highest because they
# carry the strongest replayable (or anti-replayable) signal. Pending
# defaults middle-low so unverified shapes don't crowd recall_memories.
_OUTCOME_IMPORTANCE: dict[str, int] = {
    OUTCOME_PENDING: 4,
    OUTCOME_VERIFIED: 7,
    OUTCOME_FAILED: 3,
    OUTCOME_SUPERSEDED: 1,
    OUTCOME_KNOWN_BAD: 8,
}

# Outcomes a positive few-shot recall wants. ``verified`` is the
# strongest signal, but ``pending`` shapes are also useful exemplars —
# they ran without error, just haven't survived the quiet-window yet.
# Excluding pending here would create a 3-turn dead zone right after a
# successful first run, exactly when recall is most valuable.
# Failed / superseded / known-bad stay out.
_POSITIVE_OUTCOMES: frozenset[str] = frozenset({
    OUTCOME_VERIFIED,
    OUTCOME_PENDING,
})

# Backwards-compat alias for legacy callers (e.g. older tests).
_LEGACY_OUTCOME_MAP: dict[str, str] = {
    "success": OUTCOME_PENDING,   # legacy "success" → fresh write goes to pending
    "fail": OUTCOME_FAILED,
    "fix": OUTCOME_VERIFIED,       # explicit fix is a verified replay
}


# Topics we explicitly REFUSE to record lessons for. Empty by default —
# the dispatcher's universal post-skill hook now feeds every topic that
# passes its noise filter. Add a topic here only if its lessons turn out
# to pollute recall (e.g. tag became too generic).
_TOPIC_DENYLIST: frozenset[str] = frozenset()

# Backwards-compatibility alias. Old callers (n8n_management.py) imported
# the original whitelist name; keep the symbol exposed but treat it as a
# no-op informational set so explicit topics still record.
LEARNING_TOPICS: frozenset[str] = frozenset({
    "n8n", "instagram", "email", "whatsapp",
    "browser", "web", "telegram", "lazybrain", "tasks", "vault", "computer",
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


_RUN_LOG_HEADER = "## Run log"
_RUN_LOG_CAP = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_title(topic: str, action: str, intent_slug: str) -> str:
    """One title per (topic, action, intent) triple — the upsert handle.

    Centred dot avoids collision with any user-typed `Lesson:` title.
    Empty intent_slug falls back to "(no-intent)" so the title stays
    deterministic; the same call replays into the same card.
    """
    slug = intent_slug or "no-intent"
    base_action = (action or "unknown").split(":")[0]
    return f"Skill shape · {topic}/{base_action} · {slug}"


def _build_body(
    topic: str,
    action: str,
    intent: str,
    params_block: str,
    error: str | None,
    fix_summary: str | None,
    result_snippet: str | None = None,
) -> str:
    """Render the human-readable card body. Frontmatter holds the
    machine-readable fields; this is what a user sees in the React
    panel without expanding the properties pane.

    ``result_snippet`` is the first chars of the actual tool output —
    saved on every outcome so the card carries "what came back" not just
    "what was attempted." Critical for failure replay (the brain can see
    why) and shape recall (the brain can see the response structure).
    """
    lines: list[str] = [
        f"**Topic:** {topic}",
        f"**Action:** {action}",
        f"**Intent:** {intent}",
    ]
    if error:
        lines.append(f"**Error:** {_redact(error)}")
    if fix_summary:
        lines.append(f"**Fix:** {_redact(fix_summary)}")
    if result_snippet:
        lines.append("")
        lines.append("**Output:**")
        lines.append("```")
        lines.append(_redact(result_snippet))
        lines.append("```")
    if params_block:
        lines.append("")
        lines.append("```json")
        lines.append(params_block)
        lines.append("```")
    lines.append("")
    lines.append(_RUN_LOG_HEADER)
    return "\n".join(lines)


def _append_run_log(body: str, line: str) -> str:
    """Append a single dated line under the Run log header, capped at
    ``_RUN_LOG_CAP``. Idempotent if the same iso line already trailing.
    """
    if _RUN_LOG_HEADER not in body:
        body = body.rstrip() + "\n\n" + _RUN_LOG_HEADER + "\n"
    pre, _, log = body.rpartition(_RUN_LOG_HEADER)
    rows = [r for r in log.splitlines() if r.strip().startswith("- ")]
    rows.append(f"- {line}")
    if len(rows) > _RUN_LOG_CAP:
        rows = rows[-_RUN_LOG_CAP:]
    return pre + _RUN_LOG_HEADER + "\n" + "\n".join(rows) + "\n"


async def save_skill_lesson(
    config: "Config",
    user_id: str,
    *,
    topic: str,
    action: str,
    intent: str,
    params: dict | None = None,
    outcome: str = OUTCOME_PENDING,
    error: str | None = None,
    fix_summary: str | None = None,
    result_snippet: str | None = None,
    pending_since_turn: int | None = None,
) -> str | None:
    """Upsert a skill-outcome card to LazyBrain. Returns note id or None.

    One card per ``(topic, action, intent_slug)``. Re-execution merges
    into the existing card's frontmatter (Anki-style review reinforcement)
    instead of producing a duplicate note. Never raises.

    Legacy callers passing ``outcome="success"`` are normalised to
    ``OUTCOME_PENDING`` (a fresh write awaits verification); ``"fail"``
    maps to ``OUTCOME_FAILED``; ``"fix"`` maps to ``OUTCOME_VERIFIED``.
    """
    if not topic or topic in _TOPIC_DENYLIST:
        logger.debug("skill_lesson: topic %r denylisted, skipping", topic)
        return None

    # Normalise legacy outcome strings into the new state machine.
    if outcome not in _OUTCOMES:
        if outcome in _LEGACY_OUTCOME_MAP:
            outcome = _LEGACY_OUTCOME_MAP[outcome]
        else:
            logger.debug("skill_lesson: unknown outcome %r, skipping", outcome)
            return None

    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.store import _title_key  # internal helper

        clean_params = _redact(params) if params is not None else None
        try:
            params_block = (
                json.dumps(clean_params, ensure_ascii=False, indent=2)
                if clean_params is not None else ""
            )
        except Exception:
            logger.debug(
                "[lesson] params_block json.dumps failed topic=%s action=%s, "
                "falling back to str()", topic, action, exc_info=True,
            )
            params_block = str(clean_params)[:_STRING_REDACTION_LIMIT * 4]

        slug = _intent_slug(intent)
        canonical_title = _canonical_title(topic, action, slug)
        title_key = _title_key(canonical_title)

        # Look for an existing card with the same canonical title key.
        existing = await _find_by_title_key(config, user_id, title_key)
        logger.debug(
            "[lesson] lookup topic=%s action=%s intent_slug=%s decision=%s",
            topic, action, slug, "upsert" if existing else "insert",
        )

        tags = [
            "lesson", "auto", "owner/agent",
            "kind/shape",
            f"topic/{topic}",
            f"outcome/{outcome}",
            f"action/{(action or 'unknown').split(':')[0]}",
        ]
        if slug:
            tags.append(f"intent/{slug}")

        importance = _OUTCOME_IMPORTANCE.get(outcome, 5)
        now_iso = _now_iso()

        if existing:
            # Reinforcement (Anki review): bump replay_count, append to
            # run log, merge frontmatter. Special case: a verified card
            # hit by a fresh failure does NOT flip to failed — it demotes
            # to pending and re-arms the verification window. Failed
            # cards stay failed; pending cards stay pending.
            from lazyclaw.lazybrain.frontmatter import parse_frontmatter

            content = existing.get("content") or ""
            old_props, _body, _has = parse_frontmatter(content)
            old_replay = int(old_props.get("replay_count") or 0)
            old_outcome = str(old_props.get("outcome") or "")

            effective_outcome = outcome
            effective_pending_turn = pending_since_turn
            if outcome == OUTCOME_FAILED and old_outcome == OUTCOME_VERIFIED:
                # Verified shape hit a new error — re-test it, don't bury it.
                effective_outcome = OUTCOME_PENDING
                if effective_pending_turn is None:
                    # Fall back to the live turn counter when the caller
                    # didn't carry one (e.g. manual retry path).
                    try:
                        from lazyclaw.runtime.turn_counter import current_turn
                        effective_pending_turn = current_turn(user_id)
                    except Exception:
                        logger.debug(
                            "[lesson] current_turn lookup failed for demote "
                            "topic=%s action=%s", topic, action, exc_info=True,
                        )
                        effective_pending_turn = None
                # Refresh tags + importance to match the demoted state.
                tags = [
                    "outcome/pending" if t.startswith("outcome/") else t
                    for t in tags
                ]
                importance = _OUTCOME_IMPORTANCE[OUTCOME_PENDING]

            new_body = _append_run_log(
                content, f"{now_iso} {effective_outcome}"
            )
            fm_updates: dict[str, Any] = {
                "replay_count": old_replay + 1,
                "last_used_at": now_iso,
                "outcome": effective_outcome,
                "kind": "shape",
                "topic": topic,
                "action": (action or "unknown").split(":")[0],
                "intent": intent[:120],
            }
            # Pending → expose pending_since_turn so the verifier pump
            # knows when the quiet window started. Other outcomes clear it.
            if effective_outcome == OUTCOME_PENDING:
                if effective_pending_turn is not None:
                    fm_updates["pending_since_turn"] = effective_pending_turn
                # else: leave any existing marker alone
            else:
                fm_updates["pending_since_turn"] = None

            updated = await lb_store.update_note(
                config, user_id, existing["id"],
                content=new_body,
                tags=tags,
                importance=importance,
                frontmatter_updates=fm_updates,
            )
            note_id = (updated or existing).get("id") or existing.get("id")
            try:
                lb_events.publish_note_saved(
                    user_id, note_id, canonical_title, tags,
                    source="skill_lesson_upsert",
                )
            except Exception:
                logger.debug(
                    "[lesson] publish_note_saved failed id=%s topic=%s",
                    note_id, topic, exc_info=True,
                )
            logger.info(
                "skill_lesson upserted: topic=%s action=%s outcome=%s "
                "replay_count=%d id=%s",
                topic, action, outcome, old_replay + 1, note_id,
            )
            return note_id

        # First write — create a fresh card.
        body = _build_body(
            topic, action, intent, params_block, error, fix_summary,
            result_snippet=result_snippet,
        )
        body = _append_run_log(body, f"{now_iso} {outcome}")
        frontmatter: dict[str, Any] = {
            "kind": "shape",
            "topic": topic,
            "action": (action or "unknown").split(":")[0],
            "intent": intent[:120],
            "outcome": outcome,
            "replay_count": 1,
            "first_used_at": now_iso,
            "last_used_at": now_iso,
        }
        if outcome == OUTCOME_PENDING and pending_since_turn is not None:
            frontmatter["pending_since_turn"] = pending_since_turn

        note = await lb_store.save_note(
            config, user_id,
            content=body,
            title=canonical_title,
            tags=tags,
            importance=importance,
            frontmatter=frontmatter,
        )
        try:
            lb_events.publish_note_saved(
                user_id, note["id"], note.get("title"),
                note.get("tags"), source="skill_lesson",
            )
        except Exception:
            logger.debug(
                "[lesson] publish_note_saved failed id=%s topic=%s",
                note["id"], topic, exc_info=True,
            )

        logger.info(
            "skill_lesson created: topic=%s action=%s outcome=%s id=%s",
            topic, action, outcome, note["id"],
        )
        return note["id"]
    except Exception:
        logger.warning("skill_lesson save failed", exc_info=True)
        return None


async def transition_outcome(
    config: "Config",
    user_id: str,
    *,
    lesson_id: str,
    target: str,
    reason: str | None = None,
) -> dict:
    """Promote (or demote) a lesson card's outcome state.

    Used by the Telegram ``/confirm`` and ``/reject`` commands so the user
    can close the verification loop manually for shapes the quiet-window
    pump hasn't promoted yet.

    Allowed transitions:
      pending  → verified   (``target=verified``, /confirm)
      pending  → known-bad  (``target=known-bad``, /reject)
      verified → known-bad  (user explicitly disowns a verified shape)

    Other transitions return ``{"ok": False, "reason": ...}`` without
    mutating the note. Always ownership-checks ``user_id`` against the
    note's row before doing anything.

    Returns ``{"ok": True, "lesson_id": ..., "from": ..., "to": ...,
    "title": ...}`` on success, or ``{"ok": False, "reason": ...}``.
    """
    if target not in (OUTCOME_VERIFIED, OUTCOME_KNOWN_BAD):
        return {"ok": False, "reason": f"target {target!r} not allowed"}

    try:
        from lazyclaw.lazybrain import journal as lb_journal
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    except Exception:
        logger.warning(
            "[lesson] transition_outcome lazybrain import failed lesson=%s "
            "target=%s", lesson_id, target, exc_info=True,
        )
        return {"ok": False, "reason": "lazybrain unavailable"}

    note = await lb_store.get_note(config, user_id, lesson_id)
    if not note:
        logger.debug(
            "[lesson] transition_outcome lesson not found id=%s", lesson_id,
        )
        return {"ok": False, "reason": "lesson not found"}
    tags = [str(t) for t in (note.get("tags") or [])]
    if "kind/shape" not in tags:
        logger.debug(
            "[lesson] transition_outcome not a skill-shape lesson id=%s",
            lesson_id,
        )
        return {"ok": False, "reason": "not a skill-shape lesson"}

    # Read current outcome from frontmatter (the source of truth — tag
    # may lag if a previous transition only touched one of them).
    props, _body, _has = parse_frontmatter(note.get("content") or "")
    current = str(props.get("outcome") or "")
    if current not in {OUTCOME_PENDING, OUTCOME_VERIFIED}:
        logger.debug(
            "[lesson] transition_outcome rejected id=%s current=%s target=%s",
            lesson_id, current, target,
        )
        return {
            "ok": False,
            "reason": f"can't transition from outcome={current!r}",
        }
    if current == target:
        logger.debug(
            "[lesson] transition_outcome noop id=%s outcome=%s",
            lesson_id, current,
        )
        return {
            "ok": True, "lesson_id": lesson_id,
            "from": current, "to": target,
            "title": note.get("title") or "",
            "noop": True,
        }

    # Refresh tags + importance to match new outcome.
    new_tags = [
        f"outcome/{target}" if t.startswith("outcome/") else t for t in tags
    ]
    if not any(t.startswith("outcome/") for t in new_tags):
        new_tags.append(f"outcome/{target}")

    new_importance = _OUTCOME_IMPORTANCE.get(target, 5)

    fm_updates: dict[str, Any] = {
        "outcome": target,
        "last_used_at": _now_iso(),
        "pending_since_turn": None,
    }
    if reason:
        fm_updates["transition_reason"] = reason[:240]

    try:
        await lb_store.update_note(
            config, user_id, lesson_id,
            tags=new_tags,
            importance=new_importance,
            frontmatter_updates=fm_updates,
        )
    except Exception:
        logger.warning(
            "transition_outcome update failed for lesson %s",
            lesson_id, exc_info=True,
        )
        return {"ok": False, "reason": "update failed"}

    # Append a small journal entry so the verification trail is visible
    # in today's daily page. ``[[verified]]`` / ``[[rejected]]`` wikilink
    # gives the graph an honest edge from "today" to the changed lesson.
    journal_note_id: str | None = None
    try:
        title = note.get("title") or "(untitled lesson)"
        verb = "verified" if target == OUTCOME_VERIFIED else "rejected"
        line = f"[[{verb}]] [[{title}]]"
        if reason:
            line += f" — {reason[:160]}"
        journal = await lb_journal.append_journal(config, user_id, line)
        journal_note_id = (journal or {}).get("id")
    except Exception:
        logger.debug(
            "journal append for lesson %s failed",
            lesson_id, exc_info=True,
        )

    # Typed-edge writeback: stamp a ``references`` edge from today's
    # journal to the lesson with ``source='lesson_transition'``. The
    # plain [[wikilink]] above still emits a ``wikilink`` edge — this
    # one carries provenance so the graph UI can later highlight
    # "user-confirmed" or "user-rejected" links distinctly.
    if journal_note_id:
        try:
            await lb_store.add_relation(
                config, user_id,
                from_note_id=journal_note_id,
                to_note_id=lesson_id,
                kind="references",
                source=f"lesson_{target}",
            )
        except Exception:
            logger.debug("typed-edge writeback failed", exc_info=True)

    logger.info(
        "[lesson] transition_outcome ok id=%s from=%s to=%s",
        lesson_id, current, target,
    )
    return {
        "ok": True,
        "lesson_id": lesson_id,
        "from": current,
        "to": target,
        "title": note.get("title") or "",
    }


async def _audit_dropped_lessons(
    config: "Config",
    user_id: str,
    *,
    topic: str,
    dropped: list[tuple[str, str]],
) -> None:
    """Write a debug entry to ``audit_log`` listing lessons dropped by
    ``recall_skill_lessons``'s outcome filter.

    Transparency play — without this, contested/superseded shapes vanish
    silently from recall. With it the user can see "why didn't lesson X
    surface?" via the audit page. ``dropped`` is ``[(note_id, outcome)]``.
    Best-effort, never raises.
    """
    if not dropped:
        return
    try:
        from uuid import uuid4

        from lazyclaw.db.connection import db_session as _db_session

        summary = ", ".join(f"{nid[:8]}={oc}" for nid, oc in dropped[:6])
        async with _db_session(config) as db:
            await db.execute(
                "INSERT INTO audit_log "
                "(id, user_id, action, skill_name, result_summary, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    user_id,
                    "lesson_filtered",
                    "recall_skill_lessons",
                    f"topic={topic} dropped={summary}",
                    "system",
                ),
            )
            await db.commit()
        logger.debug(
            "[lesson] audit dropped-lessons write ok topic=%s count=%d",
            topic, len(dropped),
        )
    except Exception:
        logger.debug(
            "[lesson] audit dropped-lessons failed topic=%s count=%d",
            topic, len(dropped), exc_info=True,
        )


async def _find_by_title_key(
    config: "Config", user_id: str, title_key: str | None
) -> dict | None:
    """Return the (topic, action, intent_slug) card if one exists.

    Goes through the public ``store.get_note`` after a direct title_key
    SELECT — keeps the decryption + tag-loading path consistent with how
    the rest of the system reads notes.
    """
    if not title_key:
        return None
    try:
        from lazyclaw.db.connection import db_session
        from lazyclaw.lazybrain import store as lb_store

        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id FROM notes WHERE user_id = ? AND title_key = ? LIMIT 1",
                (user_id, title_key),
            )
            row = await rows.fetchone()
        if not row:
            return None
        return await lb_store.get_note(config, user_id, row[0])
    except Exception:
        logger.debug(
            "[lesson] find_by_title_key failed title_key=%s",
            title_key[:16] if title_key else None, exc_info=True,
        )
        return None


# ── Read side ────────────────────────────────────────────────────────


async def recall_skill_lessons(
    config: "Config",
    user_id: str,
    *,
    topic: str,
    intent: str,
    k: int = 3,
    outcomes: tuple[str, ...] | None = None,
    kinds: tuple[str, ...] = ("shape",),
) -> list[dict]:
    """Return up to ``k`` past shapes for ``topic`` matching ``intent``.

    By default filters to ``kind/shape`` cards with ``outcome/verified`` —
    only confirmed-good shapes feed the few-shot pool. Pass ``outcomes``
    explicitly to widen (e.g. ``("verified", "pending")`` to include
    unverified replays during a rebuild).

    Each element carries ``{title, intent, action, outcome, params, body}``.
    Empty list on no matches, on unknown topic, or on any error.
    Excludes ``kind/known-bad`` cards from positive recall — those go
    through ``recall_known_bad_shapes`` as negative examples.
    """
    if not topic or topic in _TOPIC_DENYLIST:
        return []

    if outcomes is None:
        outcomes = tuple(_POSITIVE_OUTCOMES)
    wanted_outcomes = set(outcomes)
    wanted_kinds = {f"kind/{k}" for k in kinds}

    try:
        from lazyclaw.lazybrain import embeddings as lb_emb

        query = f"topic:{topic} {intent}"
        hit = await lb_emb.semantic_search(
            config, user_id, query,
            k=max(1, k * 3),  # widen the pool — more rows get filtered out now
            tag_prefix=f"topic/{topic}",
        )
        results = hit.get("results") or []
    except Exception:
        logger.debug(
            "[lesson] recall_skill_lessons search failed topic=%s "
            "intent_slug=%s", topic, _intent_slug(intent), exc_info=True,
        )
        return []
    logger.debug(
        "[lesson] recall_skill_lessons topic=%s intent_slug=%s k=%d "
        "raw_results=%d", topic, _intent_slug(intent), k, len(results),
    )

    out: list[dict] = []
    dropped: list[tuple[str, str]] = []
    for note in results:
        tags = [str(t) for t in (note.get("tags") or [])]
        if f"topic/{topic}" not in tags:
            continue
        # Kind filter — only kind/shape cards by default; never inject
        # known-bad as a positive example.
        if not any(t in wanted_kinds for t in tags):
            continue
        if "kind/known-bad" in tags:
            continue
        # Outcome filter — only verified shapes by default.
        this_outcome = next(
            (t.split("/", 1)[1] for t in tags if t.startswith("outcome/")),
            None,
        )
        if this_outcome not in wanted_outcomes:
            # Track silent drops so the user can see what was filtered
            # via the audit log instead of guessing why a known shape
            # didn't surface in recall.
            dropped.append((note.get("id") or "", this_outcome or "?"))
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

    logger.debug(
        "[lesson] recall_skill_lessons topic=%s matched=%d dropped=%d",
        topic, len(out), len(dropped),
    )
    if dropped:
        # Best-effort, fire-and-forget — recall must never wait on audit IO.
        try:
            import asyncio as _asyncio
            _asyncio.create_task(
                _audit_dropped_lessons(
                    config, user_id, topic=topic, dropped=dropped,
                )
            )
        except Exception:
            logger.debug(
                "[lesson] schedule audit-drop failed topic=%s count=%d",
                topic, len(dropped), exc_info=True,
            )
    return out


async def recall_known_bad_shapes(
    config: "Config",
    user_id: str,
    *,
    topic: str,
    intent: str,
    k: int = 2,
) -> list[dict]:
    """Return up to ``k`` known-bad shapes for ``topic`` to inject as
    negative examples ("Avoid this shape:") in the LLM prompt. Mirrors
    ``recall_skill_lessons`` but inverted on the known-bad filter.
    """
    if not topic or topic in _TOPIC_DENYLIST:
        return []

    try:
        from lazyclaw.lazybrain import embeddings as lb_emb

        query = f"topic:{topic} {intent}"
        hit = await lb_emb.semantic_search(
            config, user_id, query,
            k=max(1, k * 2),
            tag_prefix=f"topic/{topic}",
        )
        results = hit.get("results") or []
    except Exception:
        logger.debug(
            "[lesson] recall_known_bad failed topic=%s intent_slug=%s",
            topic, _intent_slug(intent), exc_info=True,
        )
        return []

    out: list[dict] = []
    for note in results:
        tags = [str(t) for t in (note.get("tags") or [])]
        if f"topic/{topic}" not in tags:
            continue
        if "outcome/known-bad" not in tags:
            continue
        parsed = _parse_lesson_body(note.get("content") or "")
        out.append({
            "title": note.get("title") or "",
            "intent": parsed.get("intent", ""),
            "action": parsed.get("action", ""),
            "params": parsed.get("params"),
            "body": note.get("content") or "",
            "note_id": note.get("id"),
        })
        if len(out) >= k:
            break
    logger.debug(
        "[lesson] recall_known_bad topic=%s matched=%d", topic, len(out),
    )
    return out


# Matches the body layout written by `save_skill_lesson` above. Kept
# permissive — missing fields shouldn't break recall.
_FIELD_RE = re.compile(r"^\*\*([^:*]+):\*\*\s*(.*)$", re.MULTILINE)
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

# Truncation caps applied at exemplar assembly time. The full lesson stays
# intact in the LazyBrain note for human inspection — these only shrink the
# bytes injected into the LLM context.
_EXEMPLAR_FIELD_LIMIT = 240
_EXEMPLAR_PARAMS_LIMIT = 600


def _truncate(value: str, limit: int) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


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
            logger.debug(
                "[lesson] _parse_lesson_body json parse failed, "
                "falling back to raw string", exc_info=True,
            )
            params = j.group(1)
    return {
        "topic": _truncate(fields.get("topic", ""), _EXEMPLAR_FIELD_LIMIT),
        "action": _truncate(fields.get("action", ""), _EXEMPLAR_FIELD_LIMIT),
        "intent": _truncate(fields.get("intent", ""), _EXEMPLAR_FIELD_LIMIT),
        "outcome": fields.get("outcome", ""),
        "error": _truncate(fields.get("error", ""), _EXEMPLAR_FIELD_LIMIT),
        "fix": _truncate(fields.get("fix", ""), _EXEMPLAR_FIELD_LIMIT),
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
                logger.debug(
                    "[lesson] format_lessons_as_exemplars pretty-print "
                    "failed, using str()", exc_info=True,
                )
                pretty = str(params)
            pretty = _truncate(pretty, _EXEMPLAR_PARAMS_LIMIT)
            blocks.append("Working parameters:")
            blocks.append("```json")
            blocks.append(pretty)
            blocks.append("```")
    blocks.append("\nReuse these shapes when the current request is analogous.")
    return "\n".join(blocks)


def format_known_bad_as_warnings(known_bad: list[dict]) -> str:
    """Markdown block for negative examples. Kept distinct from the
    positive-exemplar block so the LLM doesn't confuse them."""
    if not known_bad:
        return ""
    blocks: list[str] = ["## Avoid these known-bad shapes"]
    for i, lesson in enumerate(known_bad, 1):
        blocks.append(
            f"\n### Anti-example {i} — {lesson.get('intent', '(no intent)')}"
        )
        if lesson.get("action"):
            blocks.append(f"Action that failed: `{lesson['action']}`")
        params = lesson.get("params")
        if params is not None:
            try:
                pretty = json.dumps(params, ensure_ascii=False, indent=2)
            except Exception:
                logger.debug(
                    "[lesson] format_known_bad_as_warnings pretty-print "
                    "failed, using str()", exc_info=True,
                )
                pretty = str(params)
            pretty = _truncate(pretty, _EXEMPLAR_PARAMS_LIMIT)
            blocks.append("Parameters that did NOT work:")
            blocks.append("```json")
            blocks.append(pretty)
            blocks.append("```")
    blocks.append("\nDo not replay the above. Find a different shape.")
    return "\n".join(blocks)


__all__ = [
    "OUTCOME_PENDING",
    "OUTCOME_VERIFIED",
    "OUTCOME_FAILED",
    "OUTCOME_SUPERSEDED",
    "OUTCOME_KNOWN_BAD",
    "LEARNING_TOPICS",
    "save_skill_lesson",
    "recall_skill_lessons",
    "recall_known_bad_shapes",
    "transition_outcome",
    "format_lessons_as_exemplars",
    "format_known_bad_as_warnings",
]
