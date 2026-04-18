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

import json
import logging
import re
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
    r"\b(?:decided|going with|we'?ll use|i'?ll go with|chose)\s+([^\.\n]{4,140})",
    re.IGNORECASE,
)

_TIL_RE = re.compile(
    r"\b(?:TIL|today i learned|turns out|apparently|good to know)[:\- ]+"
    r"(.{4,240}?)(?:\.\s|$)",
    re.IGNORECASE | re.DOTALL,
)

_PRICE_RE = re.compile(
    r"(?P<what>[A-Z][\w\s]{2,60}?)\s+costs?\s+"
    r"(?P<cur>[$€£])(?P<amount>\d[\d,\.]*)"
    r"(?:\s*(?:per|/|a)\s+(?P<unit>\w+))?",
    re.IGNORECASE,
)

_DEADLINE_RE = re.compile(
    r"\b(?:deadline|due|by)\s+(?P<when>"
    r"(?:tomorrow|today|tonight|next (?:week|month|\w+day)|\w+ \d{1,2}(?:st|nd|rd|th)?|\d{4}-\d{2}-\d{2})"
    r")(?:\s+(?:for|:)\s+(?P<what>[^\.\n]{3,120}))?",
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
    r"\b(?:idea|should explore|worth trying|todo later)[:\- ]+([^\.\n]{5,200})",
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
# Persistence
# ---------------------------------------------------------------------------

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
    """
    try:
        captures = [c for c in extract(text) if c.confidence >= min_confidence]
        return await _persist(config, user_id, captures, trace_session_id, source)
    except Exception:
        logger.debug("auto_capture failed silently", exc_info=True)
        return []


_LLM_PROMPT = """Extract the single most important thing worth remembering from this message.
Polish the phrasing into clear English — the author may not be a native speaker.
Be conservative: if the message is vague or ambiguous, set skip=true instead of guessing.

Categories: decision | til | price | deadline | command | recipe | contact | idea | fact | task
Return strict JSON only. No prose, no code fences.
{{
  "kind": "<category>",
  "content": "<1-3 line polished markdown>",
  "title": "<short polished title, max 60 chars>",
  "importance": <1-10>,
  "tags": ["auto", "<kind>"],
  "skip": <true if unclear or not worth remembering>
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
    """
    regex_ids = await capture_text(
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
        logger.debug("auto_capture LLM fallback failed silently", exc_info=True)
        return []


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
    for cap in captures:
        note = await store.save_note(
            config,
            user_id,
            content=cap.content,
            title=cap.title,
            tags=list(cap.tags) + extra_tags,
            importance=cap.importance,
            trace_session_id=trace_session_id,
        )
        events.publish_note_saved(
            user_id,
            note["id"],
            note["title"],
            note["tags"],
            source="auto",
        )
        note_ids.append(note["id"])
    return note_ids
