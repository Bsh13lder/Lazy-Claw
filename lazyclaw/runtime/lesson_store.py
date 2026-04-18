"""Lesson storage — routes lessons to the right memory system.

Site-specific lessons → site_memory (per-domain, encrypted, success/fail tracking).
User preferences → personal_memory (global per-user, encrypted).

Both systems already inject their memories into agent context automatically:
- personal_memory via context_builder.py
- site_memory via browser_skill.py (injected into browser tool results)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.runtime.lesson_extractor import Lesson

logger = logging.getLogger(__name__)


async def store_lesson(
    config: Config,
    user_id: str,
    lesson: Lesson,
    url: str | None = None,
) -> str | None:
    """Store a lesson in the appropriate memory system.

    Returns memory ID on success, None on failure. Never raises.
    """
    try:
        if lesson.lesson_type == "site" and lesson.domain and url:
            from lazyclaw.browser.site_memory import remember

            memory_id = await remember(
                config,
                user_id,
                url,
                memory_type="custom",
                title=f"Learned: {lesson.content[:50]}",
                content={"lesson": lesson.content, "source": "correction"},
            )
            logger.info(
                "Stored site lesson for %s: %s (id=%s)",
                lesson.domain, lesson.content[:60], memory_id,
            )
            return memory_id
        else:
            from lazyclaw.memory.personal import save_memory

            memory_id = await save_memory(
                config,
                user_id,
                content=lesson.content,
                memory_type="learned_preference",
                importance=lesson.importance,
            )
            logger.info(
                "Stored preference lesson: %s (id=%s, importance=%d)",
                lesson.content[:60], memory_id, lesson.importance,
            )
            # Also mirror into LazyBrain so the user can browse + backlink
            # the lesson in the PKM UI. Fire-and-forget; matches the
            # defensive pattern used elsewhere in this module.
            try:
                from lazyclaw.lazybrain import store as lb_store
                from lazyclaw.lazybrain import events as lb_events

                tags = ["lesson", "auto", "owner/agent"]
                if lesson.lesson_type == "site" and lesson.domain:
                    tags.append(f"site/{lesson.domain}")
                note = await lb_store.save_note(
                    config,
                    user_id,
                    content=lesson.content,
                    title=f"Lesson: {lesson.content[:60]}",
                    tags=tags,
                    importance=lesson.importance,
                )
                lb_events.publish_note_saved(
                    user_id, note["id"], note["title"], note["tags"], source="lesson",
                )
            except Exception:
                logger.debug("lazybrain lesson mirror failed", exc_info=True)
            return memory_id

    except Exception as e:
        logger.warning("Failed to store lesson: %s", e)
        return None
