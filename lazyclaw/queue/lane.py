"""Per-user FIFO lane queue. Each user gets their own serial queue."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
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
            raise RuntimeError("LaneQueue not started")

        key = lane_key or user_id
        job = Job(user_id=user_id, message=message, kwargs=kwargs)
        lane = self._get_lane(key)
        await lane.put(job)
        logger.debug(
            "Enqueued job for user %s on lane %s (queue size: %d)",
            user_id, key, lane.qsize(),
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
                    logger.debug("Cleaned up idle lane %s", lane_key)
                    return
                continue

            try:
                result = await self._handler(job.user_id, job.message, **job.kwargs)
                logger.debug("Job completed for user %s (result_len=%d)", job.user_id, len(result or ""))
                if not job.result_future.done():
                    job.result_future.set_result(result)
            except Exception as e:
                logger.error("Job failed for user %s: %s", job.user_id, e, exc_info=True)
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
