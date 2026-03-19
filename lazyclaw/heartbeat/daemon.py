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
            try:
                await self._check_watchers(user_id)
            except Exception:
                logger.exception(
                    "Failed checking watchers for user %s", user_id
                )

        # Keep persistent browser alive if enabled for any user
        await self._ensure_persistent_browser()

    async def _check_due_jobs(self, user_id: str) -> None:
        """Load active jobs for a user and enqueue any that are due."""
        from lazyclaw.heartbeat import orchestrator

        key = derive_server_key(self._config.server_secret, user_id)

        # Check cron jobs (recurring)
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, cron_expression, last_run "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' AND cron_expression IS NOT NULL",
                (user_id,),
            )
            cron_jobs = await cursor.fetchall()

        for row in cron_jobs:
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

        # Check one-time reminders
        await self._check_due_reminders(user_id, key)

    async def _check_due_reminders(self, user_id: str, key: bytes) -> None:
        """Fire one-time reminders that are due, then auto-delete them."""
        from datetime import datetime, timezone
        from lazyclaw.heartbeat.orchestrator import delete_job

        now = datetime.now(timezone.utc).isoformat()

        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, next_run "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' "
                "AND job_type = 'reminder' AND next_run IS NOT NULL "
                "AND next_run <= ?",
                (user_id, now),
            )
            reminders = await cursor.fetchall()

        for row in reminders:
            job_id, enc_name, enc_instruction, next_run = row
            try:
                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name
                )
                message = (
                    decrypt(enc_instruction, key)
                    if enc_instruction and is_encrypted(enc_instruction)
                    else enc_instruction
                )

                logger.info("Reminder '%s' (%s) is due, firing", job_name, job_id)

                # Enqueue as agent message (will reach Telegram via callback)
                await self._lane_queue.enqueue(
                    user_id,
                    f"[REMINDER] {message}",
                )

                # Auto-delete — one-shot reminder, done
                await delete_job(self._config, user_id, job_id)
                logger.info("Reminder '%s' auto-deleted after firing", job_name)
            except Exception:
                logger.exception(
                    "Error processing reminder %s for user %s", job_id, user_id,
                )

    async def _check_watchers(self, user_id: str) -> None:
        """Check all active watchers for a user. Zero LLM calls."""
        import json

        from lazyclaw.browser.browser_settings import touch_browser_activity
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.browser.watcher import (
            check_watcher,
            is_check_due,
            is_watcher_expired,
        )
        from lazyclaw.heartbeat.orchestrator import delete_job, update_job

        key = derive_server_key(self._config.server_secret, user_id)

        # Fetch active watchers
        async with db_session(self._config) as db:
            cursor = await db.execute(
                "SELECT id, name, instruction, context "
                "FROM agent_jobs "
                "WHERE user_id = ? AND status = 'active' AND job_type = 'watcher'",
                (user_id,),
            )
            watchers = await cursor.fetchall()

        if not watchers:
            return

        # Need a browser for watcher checks
        port = getattr(self._config, "cdp_port", 9222)
        if not await find_chrome_cdp(port):
            # No browser — can't check. Will be launched by _ensure_persistent_browser
            return

        from lazyclaw.browser.cdp_backend import CDPBackend

        profile_dir = str(self._config.database_dir / "browser_profiles" / user_id)
        backend = CDPBackend(port=port, profile_dir=profile_dir)

        for row in watchers:
            job_id, enc_name, enc_instruction, enc_context = row
            try:
                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name or "unnamed"
                )

                # Decrypt and parse context
                raw_ctx = (
                    decrypt(enc_context, key)
                    if enc_context and is_encrypted(enc_context)
                    else enc_context or "{}"
                )
                ctx = json.loads(raw_ctx)

                # Check expiration
                if is_watcher_expired(ctx):
                    logger.info("Watcher '%s' expired, removing", job_name)
                    await delete_job(self._config, user_id, job_id)
                    await self._lane_queue.enqueue(
                        user_id,
                        f"[WATCHER] '{job_name}' has expired and stopped.",
                    )
                    continue

                # Check interval
                if not is_check_due(ctx):
                    continue

                # Run the check — zero LLM calls
                touch_browser_activity()
                changed, notification, new_ctx = await check_watcher(backend, ctx)

                # Save updated context (new last_value, last_check)
                await update_job(
                    self._config, user_id, job_id,
                    context=json.dumps(new_ctx),
                )

                if changed and notification:
                    logger.info("Watcher '%s' detected change", job_name)
                    await self._lane_queue.enqueue(
                        user_id,
                        f"[WATCHER:{job_name}] {notification}",
                    )

                    # One-shot watcher — auto-delete after first trigger
                    if new_ctx.get("one_shot"):
                        await delete_job(self._config, user_id, job_id)
                        logger.info("One-shot watcher '%s' auto-deleted", job_name)

            except Exception:
                logger.exception(
                    "Error checking watcher %s for user %s", job_id, user_id,
                )

    async def _ensure_persistent_browser(self) -> None:
        """Manage browser lifecycle based on persistence mode.

        - "on"   → restart if crashed
        - "auto" → restart if crashed AND recently active, kill if idle
        - "off"  → do nothing (on-demand only)
        """
        try:
            import asyncio

            from lazyclaw.browser.browser_settings import (
                browser_idle_seconds,
                get_browser_settings,
            )
            from lazyclaw.browser.cdp import find_chrome_cdp

            async with db_session(self._config) as db:
                cursor = await db.execute("SELECT id FROM users LIMIT 10")
                users = [r[0] for r in await cursor.fetchall()]

            port = getattr(self._config, "cdp_port", 9222)
            browser_alive = bool(await find_chrome_cdp(port))

            for user_id in users:
                settings = await get_browser_settings(self._config, user_id)
                mode = settings.get("persistent", "auto")

                if mode == "off":
                    continue

                if mode == "on":
                    # Always keep alive — restart if dead
                    if not browser_alive:
                        await self._launch_browser(user_id, port)
                    return

                if mode == "auto":
                    idle = browser_idle_seconds()
                    timeout = settings.get("idle_timeout", 600)

                    # Check if there are active watchers — keep alive
                    has_watchers = False
                    async with db_session(self._config) as db:
                        cursor = await db.execute(
                            "SELECT COUNT(*) FROM agent_jobs "
                            "WHERE user_id = ? AND job_type = 'watcher' "
                            "AND status = 'active'",
                            (user_id,),
                        )
                        row = await cursor.fetchone()
                        has_watchers = row and row[0] > 0

                    if browser_alive and idle > timeout and not has_watchers:
                        # Idle too long and no watchers — kill it
                        logger.info(
                            "Auto-closing idle browser (%.0fs idle, %ds timeout)",
                            idle, timeout,
                        )
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "pkill", "-f",
                                f"--remote-debugging-port={port}",
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            await proc.wait()
                        except Exception:
                            pass
                    elif not browser_alive and (idle < timeout or has_watchers):
                        # Browser died but still needed — restart
                        await self._launch_browser(user_id, port)
                    return

        except Exception:
            logger.debug("Persistent browser check failed", exc_info=True)

    async def _launch_browser(self, user_id: str, port: int) -> None:
        """Launch headless browser for a user."""
        from lazyclaw.browser.cdp_backend import CDPBackend

        logger.info("Launching persistent browser for user %s", user_id)
        profile_dir = str(
            self._config.database_dir / "browser_profiles" / user_id
        )
        backend = CDPBackend(port=port, profile_dir=profile_dir)
        ws_url = await backend._auto_launch_chrome()
        if ws_url:
            logger.info("Persistent browser running (CDP port %d)", port)
        else:
            logger.warning("Failed to launch persistent browser")

    async def _load_heartbeat_md(self) -> str:
        """Load the HEARTBEAT.md personality file content."""
        heartbeat_path = (
            Path(__file__).resolve().parent.parent.parent / "personality" / "HEARTBEAT.md"
        )
        if not heartbeat_path.exists():
            return ""

        return heartbeat_path.read_text(encoding="utf-8")
