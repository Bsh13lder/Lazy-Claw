"""Shared quick-note capture for Telegram (slash command + trigger phrases).

Both /note and the ``note:`` / ``idea:`` / ``remember:`` text intercept land
here so the tag scheme stays identical. The capture path is **direct**:
it talks to ``lazybrain.store.save_note`` without going through the agent
LLM — no token cost, no parser ambiguity.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# Regex matches the trigger word + optional whitespace + colon + content.
# The content group is what gets saved as the note body.
_TRIGGERS: dict[str, str] = {
    # English
    "note":     "kind/note",
    "idea":     "kind/idea",
    "remember": "kind/memory",
    "memo":     "kind/note",
    # Spanish
    "nota":     "kind/note",
    "idea-es":  "kind/idea",      # not exposed — already matches "idea"
    "recuerda": "kind/memory",
}


# Build the matcher once. Anchored at start-of-line, case-insensitive.
_TRIGGER_RE = re.compile(
    r"^\s*(note|idea|remember|memo|nota|recuerda)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def detect_trigger(text: str) -> tuple[str, str] | None:
    """Return ``(kind_tag, content)`` if the message starts with a trigger
    prefix, else ``None``. ``content`` is the rest of the message stripped.
    """
    m = _TRIGGER_RE.match(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    content = m.group(2).strip()
    if not content:
        return None
    # Map "remember" / "recuerda" → kind/memory; "idea" → kind/idea; rest → kind/note
    if word in ("remember", "recuerda"):
        kind = "kind/memory"
    elif word == "idea":
        kind = "kind/idea"
    else:
        kind = "kind/note"
    return kind, content


def _extract_inline_tags(content: str) -> tuple[list[str], str]:
    """Pull ``#hashtags`` out of the body and return them as a clean list.

    The body keeps the hashtags inline so they remain readable when the
    note is rendered — but we *also* surface them as structured tags so
    the Notes page can filter by them.
    """
    raw = re.findall(r"#([\w\-]+)", content)
    tags: list[str] = []
    for t in raw:
        t = t.strip().lower()
        if t and t not in tags:
            tags.append(t)
    return tags, content


async def save_quick_note(
    config: Config,
    user_id: str,
    content: str,
    kind_tag: str,
    *,
    source: str = "telegram",
    extra_tags: Iterable[str] | None = None,
) -> dict:
    """Persist a quick note and return the saved row.

    - Stamps ``owner/user`` (the human typed it), the supplied ``kind_tag``,
      ``source/{source}`` so the Notes page can filter by entry channel,
      plus any inline ``#hashtags``.
    - Title is the first line, capped at 80 chars. Falls back to a snippet
      of the body when the user typed only a one-liner.
    """
    from lazyclaw.lazybrain import events as lb_events
    from lazyclaw.lazybrain import store as lb_store

    inline_tags, _ = _extract_inline_tags(content)
    tags = [kind_tag, "owner/user", f"source/{source}", *inline_tags]
    if extra_tags:
        for t in extra_tags:
            t = str(t).strip()
            if t and t not in tags:
                tags.append(t)

    first_line = content.splitlines()[0].strip()
    title = first_line[:80] if first_line else content.strip()[:80] or "Quick note"

    note = await lb_store.save_note(
        config,
        user_id,
        content=content,
        title=title,
        tags=tags,
    )
    try:
        lb_events.publish_note_saved(
            user_id, note["id"], note.get("title"), note.get("tags"),
            source=source,
        )
    except Exception:
        logger.debug("note_saved event publish failed", exc_info=True)
    return note


def kind_label(kind_tag: str) -> str:
    """Human-readable name for the kind tag — used in confirmation toasts."""
    if kind_tag.endswith("/idea"):
        return "Idea"
    if kind_tag.endswith("/memory"):
        return "Memory"
    return "Note"
