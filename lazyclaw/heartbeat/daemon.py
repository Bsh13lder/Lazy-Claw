"""Background async daemon for proactive heartbeat checks and cron jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt, derive_server_key, is_encrypted
from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.cron import calculate_next_run, is_due

logger = logging.getLogger(__name__)


class HeartbeatDaemon:
    """Periodically checks for due cron jobs and enqueues them."""

    def __init__(self, config: Config, lane_queue) -> None:
        self._config = config
        self._lane_queue = lane_queue
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Launch the heartbeat loop as a background task."""
        if self._task is not None:
            logger.warning("HeartbeatDaemon already running")
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "HeartbeatDaemon started (interval=%ds)",
            self._config.heartbeat_interval,
        )

    async def stop(self) -> None:
        """Cancel the heartbeat loop and wait for clean shutdown."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.shield(self._task)
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        logger.info("HeartbeatDaemon stopped")

    async def _loop(self) -> None:
        """Infinite loop: tick then sleep."""
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("HeartbeatDaemon tick failed")
            await asyncio.sleep(self._config.heartbeat_interval)

    async def _tick(self) -> None:
        """Single heartbeat: find users with active jobs and check them."""
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT DISTINCT user_id FROM agent_jobs WHERE status = 'active'"
            )
            user_ids = [r[0] for r in await cursor.fetchall()]

        for user_id in user_ids:
            try:
                await self._check_due_jobs(user_id)
            except Exception:
                logger.exception(
                    "Failed checking due jobs for user %s", user_id
                )

    async def _check_due_jobs(self, user_id: str) -> None:
        """Load active jobs for a user and enqueue any that are due."""
        from lazyclaw.heartbeat import orchestrator

        key = derive_server_key(self._config.server_secret, user_id)

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, cron_expression, last_run "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' AND cron_expression IS NOT NULL",
                (user_id,),
            )
            jobs = await cursor.fetchall()

        for row in jobs:
            job_id, enc_name, enc_instruction, cron_expression, last_run = row

            try:
                if not is_due(cron_expression, last_run):
                    continue

                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name
                )
                instruction = (
                    decrypt(enc_instruction, key)
                    if enc_instruction and is_encrypted(enc_instruction)
                    else enc_instruction
                )

                logger.info("Job '%s' (%s) is due, enqueueing", job_name, job_id)

                await self._lane_queue.enqueue(
                    user_id, f"[JOB:{job_name}] {instruction}"
                )

                next_run = calculate_next_run(cron_expression)
                await orchestrator.mark_run(self._config, job_id, next_run)

            except Exception:
                logger.exception("Error processing job %s for user %s", job_id, user_id)

    async def _load_heartbeat_md(self) -> str:
        """Load the HEARTBEAT.md personality file content."""
        heartbeat_path = (
            Path(__file__).resolve().parent.parent.parent / "personality" / "HEARTBEAT.md"
        )
        if not heartbeat_path.exists():
            return ""

        return heartbeat_path.read_text(encoding="utf-8")
