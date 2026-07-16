"""Async helpers — fire-and-forget tasks with error logging."""

from __future__ import annotations

import asyncio
import logging
from typing import Coroutine

logger = logging.getLogger(__name__)

# Strong references prevent GC of unfinished tasks.
_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(
    coro: Coroutine,
    *,
    name: str | None = None,
) -> asyncio.Task:
    """Schedule *coro* as a background task with error logging.

    The task is kept alive (strong ref) until completion and any
    exception is logged rather than silently swallowed.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_task_done)
    logger.debug(
        "[aio] fire_and_forget scheduled name=%s pending=%d",
        task.get_name(), len(_background_tasks),
    )
    return task


def _task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        logger.debug("[aio] background task %r cancelled", task.get_name())
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task %r failed: %s",
            task.get_name(),
            exc,
            exc_info=exc,
        )
    else:
        logger.debug("[aio] background task %r completed ok", task.get_name())
