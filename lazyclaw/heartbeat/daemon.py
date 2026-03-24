"""Background async daemon for proactive heartbeat checks and cron jobs."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt, derive_server_key, is_encrypted
from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.cron import calculate_next_run, is_due

logger = logging.getLogger(__name__)

# Last watcher notification per user — agent reads this for reply context
# Format: {user_id: {"service": "whatsapp", "items": [...], "notification": "...", "timestamp": float}}
_last_watcher_context: dict[str, dict] = {}


def get_last_watcher_context(user_id: str) -> dict | None:
    """Get last watcher notification context for a user. Used by agent for reply context."""
    return _last_watcher_context.get(user_id)


def _store_watcher_context(user_id: str, service: str, items: list, notification: str) -> None:
    """Store last watcher notification so agent can reference it."""
    import time
    _last_watcher_context[user_id] = {
        "service": service,
        "items": items[:5],  # Cap stored items
        "notification": notification,
        "timestamp": time.time(),
    }


class HeartbeatDaemon:
    """Periodically checks for due cron jobs and enqueues them."""

    def __init__(self, config: Config, lane_queue, telegram_push=None) -> None:
        self._config = config
        self._lane_queue = lane_queue
        self._telegram_push = telegram_push  # async fn(text) → send to Telegram admin
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
            try:
                await self._check_mcp_watchers(user_id)
            except Exception:
                logger.exception(
                    "Failed checking MCP watchers for user %s", user_id
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

    async def _get_background_cdp(self, user_id: str):
        """Get a CDP backend for background jobs without touching the user's live Brave.

        Strategy:
        - If no browser on port 9222: launch headless on 9222 (normal path)
        - If headless browser on port 9222: reuse it directly
        - If VISIBLE browser on port 9222: copy cookies to temp dir,
          launch a separate headless instance on port 9223

        Returns (CDPBackend, temp_dir_path_or_None). Caller must clean up
        temp_dir if returned.
        """
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.browser.cdp_backend import CDPBackend

        primary_port = getattr(self._config, "cdp_port", 9222)
        profile_dir = self._config.database_dir / "browser_profiles" / user_id

        # Check if something is running on the primary port
        ws_url = await find_chrome_cdp(primary_port)

        if not ws_url:
            # Nothing running — use primary port, auto-launch will handle it
            return CDPBackend(port=primary_port, profile_dir=str(profile_dir)), None

        # Something IS running — check if it's headless
        is_headless = False
        try:
            proc = await asyncio.create_subprocess_exec(
                "ps", "aux",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            is_headless = any(
                "headless" in line and f"remote-debugging-port={primary_port}" in line
                for line in stdout.decode("utf-8", errors="replace").splitlines()
            )
        except Exception:
            # Can't tell — assume it's visible to be safe
            pass

        if is_headless:
            # Headless on primary port — safe to reuse directly
            return CDPBackend(port=primary_port, profile_dir=str(profile_dir)), None

        # Visible browser on primary port — copy cookies to temp dir,
        # launch separate headless on background port (9223)
        bg_port = primary_port + 1  # 9223
        temp_dir = None

        try:
            temp_dir = tempfile.mkdtemp(prefix="lazyclaw_bg_")
            # Skip runtime lock/socket files that can't be copied
            _SKIP_NAMES = {"SingletonSocket", "SingletonLock", "SingletonCookie", "RunningChromeVersion"}

            def _ignore_runtime(directory: str, files: list[str]) -> set[str]:
                return {f for f in files if f in _SKIP_NAMES}

            if profile_dir.exists():
                shutil.copytree(
                    str(profile_dir), f"{temp_dir}/profile",
                    dirs_exist_ok=True, ignore=_ignore_runtime,
                )
                logger.info(
                    "Copied cookies to temp profile for background CDP (port %d)",
                    bg_port,
                )
            backend = CDPBackend(port=bg_port, profile_dir=f"{temp_dir}/profile")
            return backend, temp_dir
        except Exception as exc:
            logger.warning("Failed to create background CDP: %s, falling back to primary", exc)
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return CDPBackend(port=primary_port, profile_dir=str(profile_dir)), None

    async def _cleanup_background_cdp(self, backend, temp_dir: str | None) -> None:
        """Clean up background CDP resources."""
        try:
            await backend.close()
        except Exception:
            pass
        if temp_dir:
            # Kill the background headless if we launched one
            bg_port = getattr(self._config, "cdp_port", 9222) + 1
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pkill", "-f", f"--remote-debugging-port={bg_port}",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except Exception:
                pass
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _check_watchers(self, user_id: str) -> None:
        """Check all active watchers for a user. Zero LLM calls."""
        import json

        from lazyclaw.browser.browser_settings import touch_browser_activity
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

        # Get a background-safe CDP backend (won't touch user's visible Brave)
        backend, temp_dir = await self._get_background_cdp(user_id)

        try:
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

                    # Skip MCP watchers — handled by _check_mcp_watchers()
                    if ctx.get("type") == "mcp_watcher":
                        continue

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

                        # Push directly to Telegram (no agent loop — zero tokens)
                        if self._telegram_push:
                            try:
                                logger.info("Pushing watcher notification to Telegram")
                                await self._telegram_push(
                                    f"🔔 {notification}"
                                )
                            except Exception as exc:
                                logger.warning("Telegram push failed: %s", exc)

                        # Note: NOT enqueuing to lane_queue — Telegram push
                        # is sufficient. Enqueuing triggers a full agent loop
                        # that wastes tokens and causes tool call loops.

                        # One-shot watcher — auto-delete after first trigger
                        if new_ctx.get("one_shot"):
                            await delete_job(self._config, user_id, job_id)
                            logger.info("One-shot watcher '%s' auto-deleted", job_name)

                except Exception:
                    logger.exception(
                        "Error checking watcher %s for user %s", job_id, user_id,
                    )
        finally:
            await self._cleanup_background_cdp(backend, temp_dir)

    async def _check_mcp_watchers(self, user_id: str) -> None:
        """Check all MCP-based watchers (WhatsApp, Email, etc.). Zero LLM calls."""
        import json

        from lazyclaw.heartbeat.mcp_watcher import (
            check_mcp_watcher,
            is_mcp_check_due,
            is_mcp_watcher,
            is_mcp_watcher_expired,
        )
        from lazyclaw.heartbeat.orchestrator import delete_job, update_job

        key = derive_server_key(self._config.server_secret, user_id)

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

        # Get active MCP clients
        from lazyclaw.mcp.manager import _active_clients

        for row in watchers:
            job_id, enc_name, enc_instruction, enc_context = row
            try:
                job_name = (
                    decrypt(enc_name, key)
                    if enc_name and is_encrypted(enc_name)
                    else enc_name or "unnamed"
                )
                raw_ctx = (
                    decrypt(enc_context, key)
                    if enc_context and is_encrypted(enc_context)
                    else enc_context or "{}"
                )
                ctx = json.loads(raw_ctx)

                # Only handle MCP watchers here (browser watchers handled by _check_watchers)
                if not is_mcp_watcher(ctx):
                    continue

                if is_mcp_watcher_expired(ctx):
                    logger.info("MCP watcher '%s' expired, removing", job_name)
                    await delete_job(self._config, user_id, job_id)
                    if self._telegram_push:
                        await self._telegram_push(f"MCP watcher '{job_name}' expired and stopped.")
                    continue

                if not is_mcp_check_due(ctx):
                    continue

                # Run the MCP check
                logger.info("MCP watcher '%s' checking (%s)...", job_name, ctx.get("service", "?"))
                changed, notification, new_ctx = await check_mcp_watcher(
                    ctx, _active_clients,
                )

                # Save updated context
                await update_job(
                    self._config, user_id, job_id,
                    context=json.dumps(new_ctx),
                )

                if changed and notification:
                    logger.info("MCP watcher '%s' detected change", job_name)

                    # Push to Telegram with reply hint
                    if self._telegram_push:
                        try:
                            hint = "\n\nReply here to respond (I'll send it via WhatsApp)"
                            await self._telegram_push(f"\U0001f514 {notification}{hint}")
                        except Exception as exc:
                            logger.warning("Telegram push failed: %s", exc)

                    # Store last notification so agent has context for user replies
                    _store_watcher_context(user_id, ctx.get("service", ""), [], notification)

                    # Auto-reply: enqueue to agent if instruction provided
                    auto_reply = ctx.get("auto_reply")
                    if auto_reply and self._lane_queue:
                        await self._lane_queue.enqueue(
                            user_id,
                            f"[MCP_WATCHER] New {service} messages. {auto_reply}\n\n{notification}",
                        )

                    if new_ctx.get("one_shot"):
                        await delete_job(self._config, user_id, job_id)
                        logger.info("One-shot MCP watcher '%s' auto-deleted", job_name)

            except Exception:
                logger.exception(
                    "Error checking MCP watcher %s for user %s", job_id, user_id,
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
                    timeout = settings.get("idle_timeout", 3600)  # 1 hour default

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

                    # Check if there are running background tasks — keep alive
                    has_bg_tasks = False
                    async with db_session(self._config) as db:
                        cursor = await db.execute(
                            "SELECT COUNT(*) FROM background_tasks "
                            "WHERE user_id = ? AND status = 'running'",
                            (user_id,),
                        )
                        row = await cursor.fetchone()
                        has_bg_tasks = row and row[0] > 0

                    if browser_alive and idle > timeout and not has_watchers and not has_bg_tasks:
                        # Idle too long and no watchers — kill it
                        logger.info(
                            "Auto-closing idle browser (%.0fs idle, %ds timeout)",
                            idle, timeout,
                        )
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "ps", "aux",
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            stdout, _ = await proc.communicate()
                            import signal
                            for line in stdout.decode("utf-8", errors="replace").splitlines():
                                if f"remote-debugging-port={port}" in line:
                                    parts = line.split()
                                    if len(parts) > 1:
                                        try:
                                            os.kill(int(parts[1]), signal.SIGTERM)
                                        except (ProcessLookupError, ValueError):
                                            pass
                        except Exception:
                            pass
                    elif not browser_alive and (idle < timeout or has_watchers or has_bg_tasks):
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
