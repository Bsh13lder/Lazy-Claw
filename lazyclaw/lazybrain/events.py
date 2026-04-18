"""Zero-token UI events for LazyBrain.

Reuses the browser event bus — the chat sidebar already subscribes to it, so
"Agent saved note: X" chips render for free.  Events never enter LLM context.
"""
from __future__ import annotations

import logging

from lazyclaw.browser.event_bus import BrowserEvent, publish

logger = logging.getLogger(__name__)


def publish_note_saved(
    user_id: str,
    note_id: str,
    title: str | None,
    tags: list[str] | None = None,
    source: str = "user",
) -> None:
    """Publish a `note_saved` canvas event. Fire-and-forget."""
    if not user_id:
        return
    try:
        publish(
            BrowserEvent(
                user_id=user_id,
                kind="note_saved",
                action="save_note",
                target=note_id,
                detail=f"Saved: {title or '(untitled)'}",
                extra={
                    "note_id": note_id,
                    "tags": tags or [],
                    "source": source,
                },
            )
        )
    except Exception:
        logger.debug("Failed to publish note_saved event", exc_info=True)


def publish_note_deleted(user_id: str, note_id: str, title: str | None) -> None:
    if not user_id:
        return
    try:
        publish(
            BrowserEvent(
                user_id=user_id,
                kind="note_deleted",
                action="delete_note",
                target=note_id,
                detail=f"Deleted: {title or '(untitled)'}",
                extra={"note_id": note_id},
            )
        )
    except Exception:
        logger.debug("Failed to publish note_deleted event", exc_info=True)
