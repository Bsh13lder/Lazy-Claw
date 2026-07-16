"""Per-user FIFO lane queue. Each user gets their own serial queue."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """A single queued message to process."""
    user_id: str
    message: str
    kwargs: dict = field(default_factory=dict)
    result_future: concurrent.futures.Future = field(
        default_factory=concurrent.futures.Future, repr=False,
    )


class LaneQueue:
    """In-memory per-user FIFO queue. Messages for the same user process serially.
    Messages for different users process concurrently."""

    def __init__(self) -> None:
        self._lanes: dict[str, asyncio.Queue[Job]] = {}
        self._processors: dict[str, asyncio.Task] = {}
        self._handler: Callable[[str, str], Awaitable[str]] | None = None
        self._running = False

    def set_handler(self, handler: Callable[[str, str], Awaitable[str]]) -> None:
        """Set the message handler (typically agent.process_message)."""
        self._handler = handler

    async def enqueue(
        self, user_id: str, message: str, *, lane_key: str | None = None, **kwargs
    ) -> str:
        """Enqueue a message and wait for the result. Returns the agent response.

        ``lane_key`` controls which FIFO lane the job runs on; it defaults to
        ``user_id``. Pass a distinct ``lane_key`` (e.g. ``f"{user_id}:heartbeat"``)
        to run daemon-originated work in parallel with the user's foreground
        chat WITHOUT corrupting the job's identity — the handler always receives
        the bare ``user_id``. Conflating the two broke get_user_dek (commit
        33db41a → ``User <id>:heartbeat not found``).
        """
        if not self._running:
            logger.warning(
                "[queue] enqueue rejected — LaneQueue not started "
                "(user=%s lane=%s)",
                user_id, lane_key or user_id,
            )
            raise RuntimeError("LaneQueue not started")

        key = lane_key or user_id
        job = Job(user_id=user_id, message=message, kwargs=kwargs)
        lane = self._get_lane(key)
        await lane.put(job)
        logger.debug(
            "[queue] enqueue user=%s lane=%s lane_depth=%d total_depth=%d",
            user_id, key, lane.qsize(), self.queue_depth,
        )

        # Await thread-safe future from async context
        loop = asyncio.get_running_loop()
        return await asyncio.wrap_future(job.result_future, loop=loop)

    def _get_lane(self, lane_key: str) -> asyncio.Queue[Job]:
        """Get or create a lane keyed by ``lane_key`` (not necessarily a user_id)."""
        if lane_key not in self._lanes:
            self._lanes[lane_key] = asyncio.Queue()
            # Start a processor task for this lane
            self._processors[lane_key] = asyncio.create_task(
                self._process_lane(lane_key)
            )
            logger.debug(
                "[queue] new lane started key=%s active_lanes=%d",
                lane_key, len(self._lanes),
            )
        return self._lanes[lane_key]

    async def _process_lane(self, lane_key: str) -> None:
        """Process jobs in a lane serially. Each job carries its own bare
        ``user_id`` (the handler's identity), independent of ``lane_key``."""
        lane = self._lanes[lane_key]
        while self._running:
            try:
                job = await asyncio.wait_for(lane.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # Clean up idle lanes
                if lane.empty():
                    del self._lanes[lane_key]
                    del self._processors[lane_key]
                    logger.debug("[queue] idle lane cleaned up key=%s", lane_key)
                    return
                continue

            logger.debug(
                "[queue] dequeue user=%s lane=%s remaining_depth=%d",
                job.user_id, lane_key, lane.qsize(),
            )
            _t0 = time.monotonic()
            try:
                result = await self._handler(job.user_id, job.message, **job.kwargs)
                logger.debug(
                    "[queue] job done user=%s lane=%s result_len=%d elapsed_ms=%.0f",
                    job.user_id, lane_key, len(result or ""),
                    (time.monotonic() - _t0) * 1000,
                )
                if not job.result_future.done():
                    job.result_future.set_result(result)
            except Exception as e:
                logger.error(
                    "[queue] job failed user=%s lane=%s err_type=%s elapsed_ms=%.0f: %s",
                    job.user_id, lane_key, type(e).__name__,
                    (time.monotonic() - _t0) * 1000, e, exc_info=True,
                )
                if not job.result_future.done():
                    job.result_future.set_result(f"Error processing message: {e}")
            finally:
                lane.task_done()

    @property
    def queue_depth(self) -> int:
        """Total pending messages across all user lanes."""
        return sum(q.qsize() for q in self._lanes.values())

    async def start(self) -> None:
        """Start the queue."""
        self._running = True
        logger.info("LaneQueue started")

    async def stop(self) -> None:
        """Stop the queue and cancel all processors."""
        self._running = False
        for task in self._processors.values():
            task.cancel()
        if self._processors:
            await asyncio.gather(*self._processors.values(), return_exceptions=True)
        self._processors.clear()
        self._lanes.clear()
        logger.info("LaneQueue stopped")
