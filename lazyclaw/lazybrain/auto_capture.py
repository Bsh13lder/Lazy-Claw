"""Auto-capture: detect the *important* things in a message and turn them
into notes without the user asking.

Two layers:

1. **Cheap regex classifier** — catches decisions, TILs, prices, deadlines,
   commands, URLs-with-context, recipes. No LLM, ~0.1 ms per message.
2. **LLM fallback** (opt-in via caller) — for ambiguous text the caller
   passes in the user's configured :class:`EcoRouter`, which routes the
   extraction call via ``ROLE_WORKER`` — so the user's own worker model
   (Gemma 4 E2B locally in HYBRID, Haiku in FULL, Claude CLI in CLAUDE,
   etc.) handles it.  No hardcoded model. Fire-and-forget, never raises.

Every auto-captured note carries the ``#auto`` tag plus a type-specific
tag (``#decision``, ``#til``, ``#price``, …) so the user can audit and
prune them from the UI.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from lazyclaw.config import Config
from lazyclaw.lazybrain import events, store

if TYPE_CHECKING:
    from lazyclaw.llm.eco_router import EcoRouter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capture types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capture:
    """One thing the system decided is worth remembering."""

    kind: str            # decision | til | price | deadline | command | recipe | contact | idea
    content: str         # the note body we will write
    title: str | None    # optional explicit title
    tags: tuple[str, ...]
    importance: int      # 1–10
    confidence: float    # 0.0–1.0 — threshold governs whether we actually save


# ---------------------------------------------------------------------------
# Regex catalog — deliberately conservative.  Too eager = garbage PKM.
# ---------------------------------------------------------------------------

_DECISION_RE = re.compile(
    r"\b(?:"
    # English
    r"decided|going with|we'?ll use|i'?ll go with|chose"
    # Spanish — decidí / decidido / vamos con / voy a usar / elegí / escogí / opté por
    r"|decid[íiío](?:do|da)?|vamos con|voy a usar|eleg[íi]|escog[íi]|opt[éeó] por"
    r")\s+([^\.\n]{4,140})",
    re.IGNORECASE,
)

_TIL_RE = re.compile(
    r"\b(?:"
    # English
    r"TIL|today i learned|turns out|apparently|good to know"
    # Spanish — aprendí hoy / hoy aprendí / resulta que / al parecer / bueno saber
    r"|aprend[íi] hoy|hoy aprend[íi]|resulta que|al parecer|bueno saber"
    r")[:\- ]+(.{4,240}?)(?:\.\s|$)",
    re.IGNORECASE | re.DOTALL,
)

_PRICE_RE = re.compile(
    r"(?P<what>[A-Za-zÁÉÍÓÚÑáéíóúñ][\wÁÉÍÓÚÑáéíóúñ\s]{2,60}?)\s+"
    # English: costs   |   Spanish: cuesta / vale / sale por
    r"(?:costs?|cuesta|vale|sale por)\s+"
    r"(?P<cur>[$€£])(?P<amount>\d[\d,\.]*)"
    # per / / / a   (English)   |   por / al / la / el   (Spanish)
    r"(?:\s*(?:per|/|a|por|al|la|el)\s+(?P<unit>\w+))?",
    re.IGNORECASE,
)

_DEADLINE_RE = re.compile(
    r"\b(?:"
    # English
    r"deadline|due|by"
    # Spanish — fecha límite / antes del / para el / vence / hasta el
    r"|fecha l[íi]mite|antes del|para el|vence(?: el)?|hasta el"
    r")\s+(?P<when>"
    r"(?:tomorrow|today|tonight|next (?:week|month|\w+day)"
    r"|ma[ñn]ana|hoy|esta noche|la pr[óo]xima semana|el pr[óo]ximo mes"
    r"|\w+ \d{1,2}(?:st|nd|rd|th)?|\d{1,2} de \w+|\d{4}-\d{2}-\d{2})"
    r")(?:\s+(?:for|:|para)\s+(?P<what>[^\.\n]{3,120}))?",
    re.IGNORECASE,
)

_COMMAND_RE = re.compile(
    r"\b(?:run|use|execute|cmd)[:\- ]*`([^`\n]{5,200})`",
    re.IGNORECASE,
)

_RECIPE_HEADER_RE = re.compile(
    r"(?P<title>[A-Z][\w \-&]{2,60})\s*(?:recipe|how[- ]to)[: \-]*\n"
    r"(?P<body>(?:\s*[-*\d]\..+\n?){2,})",
    re.IGNORECASE,
)

_URL_CONTEXT_RE = re.compile(
    r"(?:see|reference|link|useful)[:\- ]+"
    r"(?P<url>https?://\S{10,200})",
    re.IGNORECASE,
)

_CONTACT_RE = re.compile(
    r"(?P<name>[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)"
    r"(?:'s)?\s+(?:phone|number|email)\s+is\s+"
    r"(?P<contact>[\+\w\.\-@\d\s]{6,60})",
)

_IDEA_RE = re.compile(
    r"\b(?:"
    # English
    r"idea|should explore|worth trying|todo later"
    # Spanish — idea / habría que probar / deberíamos / sería bueno / pendiente
    r"|habr[íi]a que probar|deber[íi]amos|ser[íi]a bueno|pendiente"
    r")[:\- ]+([^\.\n]{5,200})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _clip(text: str, width: int = 280) -> str:
    text = " ".join(text.strip().rstrip(".,;:!? ").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def extract(text: str) -> list[Capture]:
    """Run every detector. Returns zero or more Captures."""
    if not text or len(text.strip()) < 8:
        return []

    out: list[Capture] = []

    for m in _DECISION_RE.finditer(text):
        body = _clip(m.group(1))
        out.append(
            Capture(
                kind="decision",
                content=f"**Decision:** {body}",
                title=f"Decision: {_clip(body, 60)}",
                tags=("auto", "decision"),
                importance=7,
                confidence=0.82,
            )
        )

    for m in _TIL_RE.finditer(text):
        body = _clip(m.group(1))
        out.append(
            Capture(
                kind="til",
                content=f"**TIL** — {body}",
                title=f"TIL: {_clip(body, 60)}",
                tags=("auto", "til"),
                importance=5,
                confidence=0.85,
            )
        )

    for m in _PRICE_RE.finditer(text):
        what = _clip(m.group("what"))
        cur = m.group("cur")
        amount = m.group("amount")
        unit = m.group("unit") or ""
        unit_str = f" / {unit}" if unit else ""
        out.append(
            Capture(
                kind="price",
                content=f"**{what}** = {cur}{amount}{unit_str}",
                title=f"Price: {what}",
                tags=("auto", "price"),
                importance=6,
                confidence=0.75,
            )
        )

    for m in _DEADLINE_RE.finditer(text):
        when = m.group("when")
        what = m.group("what") or "(no subject captured)"
        out.append(
            Capture(
                kind="deadline",
                content=f"**Deadline** {when} — {what}",
                title=f"Deadline: {_clip(what, 50)}",
                tags=("auto", "deadline"),
                importance=8,
                confidence=0.8,
            )
        )

    for m in _COMMAND_RE.finditer(text):
        cmd = m.group(1).strip()
        out.append(
            Capture(
                kind="command",
                content=f"```\n{cmd}\n```",
                title=f"cmd: {_clip(cmd, 40)}",
                tags=("auto", "command", "snippet"),
                importance=5,
                confidence=0.9,
            )
        )

    for m in _RECIPE_HEADER_RE.finditer(text):
        title = _clip(m.group("title"), 60)
        body = m.group("body").strip()
        out.append(
            Capture(
                kind="recipe",
                content=f"# {title}\n\n{body}",
                title=title,
                tags=("auto", "recipe"),
                importance=6,
                confidence=0.7,
            )
        )

    for m in _URL_CONTEXT_RE.finditer(text):
        url = m.group("url")
        out.append(
            Capture(
                kind="url",
                content=f"Reference: {url}",
                title=f"ref: {url[:60]}",
                tags=("auto", "reference"),
                importance=4,
                confidence=0.7,
            )
        )

    for m in _CONTACT_RE.finditer(text):
        name = _clip(m.group("name"), 40)
        contact = _clip(m.group("contact"), 80)
        out.append(
            Capture(
                kind="contact",
                content=f"**{name}** — {contact}",
                title=f"Contact: {name}",
                tags=("auto", "contact"),
                importance=6,
                confidence=0.78,
            )
        )

    for m in _IDEA_RE.finditer(text):
        body = _clip(m.group(1))
        out.append(
            Capture(
                kind="idea",
                content=f"💡 {body}",
                title=f"Idea: {_clip(body, 60)}",
                tags=("auto", "idea"),
                importance=4,
                confidence=0.6,
            )
        )

    return _dedupe(out)


def _dedupe(captures: Iterable[Capture]) -> list[Capture]:
    """Drop duplicates by (kind, clipped content). Preserves order."""
    seen: set[tuple[str, str]] = set()
    out: list[Capture] = []
    for c in captures:
        key = (c.kind, _clip(c.content, 120).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Content-addressable dedup key
# ---------------------------------------------------------------------------
#
# The original writers inserted one note per occurrence. The only guard was
# ``_is_recent_duplicate``, a 7-day window keyed on the *title* — which broke
# whenever two distinct captures shared a title (``deadline: (no subject
# captured)``) or when the same content recurred outside the window
# (``visits: www.upwork.com`` ×6). The fix mirrors ``skill_lesson.py`` and
# ``lesson_store.py``: a stable title derived from a hash of ``(kind, full
# normalized content)`` so identical captures collapse onto ONE note via the
# indexed ``title_key`` column, while genuinely different content keeps its
# own row.

_WS_RE = re.compile(r"\s+")
_CONTENT_HASH_LEN = 12


def _normalize_for_key(text: str) -> str:
    """Collapse whitespace + lowercase so trivial formatting drift on the
    same capture still maps to one key."""
    return _WS_RE.sub(" ", (text or "").strip()).lower()


def capture_content_key(kind: str, content: str) -> str:
    """Deterministic short digest of ``(kind, normalized content)``.

    Two captures with the same kind AND the same content (modulo whitespace
    / case) share a key; anything else does not. Stable across processes —
    unlike the old title-prefix slice."""
    payload = f"{(kind or '').strip().lower()}\n{_normalize_for_key(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_CONTENT_HASH_LEN]


def capture_canonical_title(kind: str, content: str, base_title: str | None) -> str:
    """Content-addressable title for an auto-capture.

    Keeps the human-readable ``base_title`` (or ``"{kind}:"`` when absent)
    for the PKM list view, then appends ``[<hash>]``. The hash is the
    load-bearing part: re-capturing the same fact yields the same title →
    same ``title_key`` → upsert instead of a new row.
    """
    display = (base_title or f"{kind}:").strip()
    return f"{display} [{capture_content_key(kind, content)}]"


# ---------------------------------------------------------------------------
# Flood control — per-user hourly cap
# ---------------------------------------------------------------------------
#
# Auto-capture fires on EVERY user message (``runtime/agent.py:~7986``,
# fire-and-forget). A chatty hour therefore mints a note per turn, and the
# 2026-08-14 audit measured the result: 93.5% of the store (2,676 of 2,863
# notes) is auto-captured. Retrieval quality is a signal-to-noise problem,
# so the write side gets a ceiling.
#
# Deliberately in-memory: no DB round trip on the hot fire-and-forget path,
# and a restart simply reopens the window (fail-open — losing the counter
# can only under-throttle, never drop a user's real note permanently since
# the same message is never re-processed anyway).

#: Captures allowed per user per rolling hour. Override with
#: ``LAZYCLAW_AUTOCAPTURE_HOURLY_CAP`` (``0`` or negative disables the cap).
DEFAULT_AUTOCAPTURE_HOURLY_CAP = 12

_CAPTURE_WINDOW_SECONDS = 3600.0

#: Bound the dict so a multi-user deployment can't grow it forever
#: (mirrors ``heartbeat/daemon.py:_MAX_WATCHER_CONTEXT_USERS``).
_MAX_THROTTLE_USERS = 500

# user_id -> (window_start_monotonic, count_in_window). Values are replaced
# wholesale, never mutated in place.
_capture_windows: dict[str, tuple[float, int]] = {}


def autocapture_hourly_cap() -> int:
    """Resolve the per-user hourly cap from the environment.

    Read per call (not cached at import) so a deployment can flip the knob
    without a rebuild, and so tests can monkeypatch the env var.
    Unparseable values fall back to the default rather than raising.
    """
    raw = os.environ.get("LAZYCLAW_AUTOCAPTURE_HOURLY_CAP", "").strip()
    if not raw:
        return DEFAULT_AUTOCAPTURE_HOURLY_CAP
    try:
        return int(raw)
    except ValueError:
        logger.debug(
            "invalid LAZYCLAW_AUTOCAPTURE_HOURLY_CAP=%r; using default %d",
            raw, DEFAULT_AUTOCAPTURE_HOURLY_CAP,
        )
        return DEFAULT_AUTOCAPTURE_HOURLY_CAP


def reset_capture_throttle(user_id: str | None = None) -> None:
    """Clear throttle state (all users, or just ``user_id``). Test seam."""
    if user_id is None:
        _capture_windows.clear()
    else:
        _capture_windows.pop(user_id, None)


def _prune_expired_windows(now: float) -> None:
    """Drop windows that have already rolled over, then hard-cap the dict."""
    expired = [
        uid for uid, (start, _) in _capture_windows.items()
        if now - start >= _CAPTURE_WINDOW_SECONDS
    ]
    for uid in expired:
        _capture_windows.pop(uid, None)
    if len(_capture_windows) >= _MAX_THROTTLE_USERS:
        oldest = min(_capture_windows, key=lambda u: _capture_windows[u][0])
        _capture_windows.pop(oldest, None)


def _consume_capture_budget(user_id: str) -> bool:
    """Claim one capture slot for ``user_id``. False when the cap is hit.

    Monotonic rolling window: the first capture of a window stamps its
    start; once ``_CAPTURE_WINDOW_SECONDS`` elapse the window resets and the
    budget refills. ``time.monotonic`` (not wall clock) so an NTP step or a
    laptop suspend can't retroactively widen or shrink the window.
    """
    cap = autocapture_hourly_cap()
    if cap <= 0:
        return True  # cap disabled

    now = time.monotonic()
    window = _capture_windows.get(user_id)
    if window is None or now - window[0] >= _CAPTURE_WINDOW_SECONDS:
        if window is None:
            _prune_expired_windows(now)
        _capture_windows[user_id] = (now, 1)
        return True

    start, count = window
    if count >= cap:
        return False
    _capture_windows[user_id] = (start, count + 1)
    return True


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _capture_text_unthrottled(
    config: Config,
    user_id: str,
    text: str,
    *,
    min_confidence: float,
    trace_session_id: str | None,
    source: str,
) -> list[str]:
    """Regex tier body, with the hourly cap already claimed by the caller.

    Split out so :func:`capture_text_with_llm` consumes exactly ONE slot per
    message instead of double-counting through its inner regex pass.
    """
    try:
        captures = [c for c in extract(text) if c.confidence >= min_confidence]
        return await _persist(config, user_id, captures, trace_session_id, source)
    except Exception:
        logger.warning(
            "auto_capture regex tier failed for user %s", user_id, exc_info=True,
        )
        return []


async def capture_text(
    config: Config,
    user_id: str,
    text: str,
    *,
    min_confidence: float = 0.7,
    trace_session_id: str | None = None,
    source: str = "chat",
) -> list[str]:
    """Run all regex detectors on ``text`` and save any high-confidence hits.

    Returns the list of newly-created note IDs. Silent on failure.
    Pure regex — no LLM. Call :func:`capture_text_with_llm` for the
    worker-routed fallback.

    Rate-limited per user (see :func:`_consume_capture_budget`) — over the
    hourly cap this returns ``[]`` without touching the DB.
    """
    if not _consume_capture_budget(user_id):
        logger.debug(
            "auto_capture skipped for user %s — hourly cap (%d) reached",
            user_id, autocapture_hourly_cap(),
        )
        return []
    return await _capture_text_unthrottled(
        config, user_id, text,
        min_confidence=min_confidence,
        trace_session_id=trace_session_id,
        source=source,
    )


_LLM_PROMPT = """Extract the single most important thing worth remembering from this message.

LANGUAGE RULE — write `content` and `title` in the SAME language as the message.
If the message is in Spanish, reply in Spanish. If transliterated Georgian
(Latin-script Georgian, e.g. "gadavts'q'vit'e"), keep that script. Never
translate the user's own words into English. Only fix typos and obvious
grammar slips — preserve voice and idiom.

Be conservative — set skip=true only if the message has NO concrete fact,
decision, deadline, price, idea, contact, command, or learning. A message
in non-English with a clear point is NOT vague — capture it in its language.

Categories: decision | til | price | deadline | command | recipe | contact | idea | fact | task
Return strict JSON only. No prose, no code fences.
{{
  "kind": "<category>",
  "content": "<1-3 line markdown in the SAME LANGUAGE as the message>",
  "title": "<short title, max 60 chars, SAME LANGUAGE as the message>",
  "importance": <1-10>,
  "tags": ["auto", "<kind>"],
  "skip": <true ONLY if message has zero memorable content>
}}

Message:
{text}

JSON:"""


async def capture_text_with_llm(
    config: Config,
    user_id: str,
    text: str,
    eco_router: "EcoRouter",
    *,
    min_confidence: float = 0.6,
    trace_session_id: str | None = None,
    source: str = "chat",
) -> list[str]:
    """Regex pass first; if nothing hit, ask the user's worker model via EcoRouter.

    Routes through :class:`EcoRouter` with ``role=ROLE_WORKER`` — so the
    extraction runs on whichever worker the user's ECO mode has configured
    (local Gemma in HYBRID, Haiku in FULL, Claude CLI in CLAUDE, etc.).
    Never hardcodes a model. Fire-and-forget on failure.

    The hourly cap is claimed ONCE here and the inner regex pass runs
    unthrottled — gating at this entry point (rather than only inside
    ``capture_text``) is what keeps a throttled message from still paying
    for the 1–3s worker-LLM round trip.
    """
    if not _consume_capture_budget(user_id):
        logger.debug(
            "auto_capture (llm tier) skipped for user %s — hourly cap (%d) reached",
            user_id, autocapture_hourly_cap(),
        )
        return []

    regex_ids = await _capture_text_unthrottled(
        config,
        user_id,
        text,
        min_confidence=min_confidence,
        trace_session_id=trace_session_id,
        source=source,
    )
    if regex_ids:
        return regex_ids
    if len(text.strip()) < 40:
        return []  # too short to bother the LLM with

    try:
        from lazyclaw.llm.eco_router import ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage

        messages = [
            LLMMessage(role="system", content="You extract single-item memories. Output JSON only."),
            LLMMessage(role="user", content=_LLM_PROMPT.format(text=text[:1200])),
        ]
        response = await eco_router.chat(messages, user_id=user_id, role=ROLE_WORKER)
        if not response or not response.content:
            return []
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        if data.get("skip") or not data.get("content"):
            return []

        cap = Capture(
            kind=str(data.get("kind", "fact"))[:20],
            content=str(data["content"])[:800],
            title=(str(data["title"])[:120] if data.get("title") else None),
            tags=tuple(str(t)[:40] for t in (data.get("tags") or ["auto", "fact"])),
            importance=max(1, min(10, int(data.get("importance", 5)))),
            confidence=0.75,
        )
        return await _persist(config, user_id, [cap], trace_session_id, source)
    except Exception:
        logger.warning(
            "auto_capture LLM fallback failed for user %s", user_id, exc_info=True,
        )
        return []


async def _find_note_by_title_key(
    config: Config, user_id: str, title_key: str | None
) -> dict | None:
    """Return the existing note for ``title_key`` (decrypted) or None.

    Direct SELECT on the indexed plaintext ``title_key`` column → public
    ``get_note``. Mirrors the lookup in ``lesson_store`` and
    ``skill_lesson``. Never raises — a lookup failure degrades to "no
    existing note" so the caller falls through to a normal insert.
    """
    if not title_key:
        return None
    try:
        from lazyclaw.db.connection import db_session

        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id FROM notes WHERE user_id = ? AND title_key = ? LIMIT 1",
                (user_id, title_key),
            )
            row = await rows.fetchone()
        if not row:
            return None
        return await store.get_note(config, user_id, row[0])
    except Exception:
        logger.debug("auto_capture find_by_title_key failed", exc_info=True)
        return None


async def _persist(
    config: Config,
    user_id: str,
    captures: list[Capture],
    trace_session_id: str | None,
    source: str,
) -> list[str]:
    if not captures:
        return []
    # auto_capture scans the user's own messages — origin is user, not agent.
    extra_tags = [f"source/{source}", "owner/user"]
    note_ids: list[str] = []
    # Within-call dedup: two captures from the SAME message that hash to the
    # same content key must not race each other into two rows.
    seen_keys: set[str] = set()
    for cap in captures:
        canonical_title = capture_canonical_title(cap.kind, cap.content, cap.title)
        title_key = store._title_key(canonical_title)
        if title_key in seen_keys:
            continue
        seen_keys.add(title_key)

        tags = list(cap.tags) + extra_tags

        # Content-addressable UPSERT: re-capturing the same (kind, content)
        # refreshes the existing card instead of stacking a duplicate. The
        # title carries the content hash, so the lookup is a cheap indexed
        # title_key hit — no decrypt loop. Replaces the old brittle 7-day
        # title-window guard (`_is_recent_duplicate`), which both missed
        # cross-window repeats and over-collapsed distinct same-title
        # captures.
        existing = await _find_note_by_title_key(config, user_id, title_key)
        if existing is not None:
            merged_tags = list({*(existing.get("tags") or []), *tags})
            note = await store.update_note(
                config,
                user_id,
                existing["id"],
                content=cap.content,
                tags=merged_tags,
                importance=cap.importance,
            ) or existing
            logger.debug(
                "auto_capture upserted existing capture: %s", canonical_title,
            )
        else:
            note = await store.save_note(
                config,
                user_id,
                content=cap.content,
                title=canonical_title,
                tags=tags,
                importance=cap.importance,
                trace_session_id=trace_session_id,
            )
        events.publish_note_saved(
            user_id,
            note["id"],
            note.get("title"),
            note.get("tags"),
            source="auto",
        )
        note_ids.append(note["id"])
        # Stitch into today's journal so the today node has live edges in
        # the graph instead of being an empty stub. Best-effort.
        if note.get("title"):
            try:
                from lazyclaw.lazybrain import journal as _journal
                await _journal.link_note_today(
                    config, user_id, title=note["title"], kind=cap.kind,
                )
            except Exception:
                logger.debug("link_note_today failed", exc_info=True)
    return note_ids
