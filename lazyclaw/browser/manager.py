"""Persistent browser session manager — one Chromium instance per user.

Extracted from LazyTasker's PersistentBrowserManager. Manages lifecycle,
stealth fingerprinting, profile persistence, and idle cleanup.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
SESSION_IDLE_TIMEOUT = 1800  # 30 minutes
SESSION_CLEANUP_INTERVAL = 300  # 5 minutes
CHROME_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")
MODERN_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class PersistentBrowserManager:
    """Manages a single user's persistent browser session with robust lifecycle."""

    def __init__(self, user_id: str, database_dir: Path) -> None:
        self.user_id = user_id
        self.profile_dir = database_dir / "browser_profiles" / user_id
        self._browser: Any | None = None
        self._last_activity: datetime | None = None

    async def get_browser(self) -> tuple[Any, bool]:
        """Get existing browser if alive, or create new one.

        Returns (browser, is_new).
        """
        from browser_use import Browser

        if self._browser and await self.is_alive():
            self.touch()
            logger.info("Reusing browser session for user %s", self.user_id)
            return self._browser, False

        if self._browser:
            logger.info("Browser for user %s is dead, creating fresh", self.user_id)
            await self._force_cleanup()

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        await self.cleanup_locks()
        await self.kill_orphaned_processes()

        vp_width = random.randint(1280, 1440)
        vp_height = random.randint(720, 900)

        try:
            from browser_use import BrowserProfile

            profile = BrowserProfile(
                headless=True,
                window_size={"width": vp_width, "height": vp_height},
                disable_security=True,
                user_data_dir=str(self.profile_dir),
                user_agent=MODERN_USER_AGENT,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ],
            )
            self._browser = Browser(browser_profile=profile, keep_alive=True)
            logger.info(
                "Created stealth browser for user %s (profile: %s)",
                self.user_id,
                self.profile_dir,
            )
        except (ImportError, TypeError) as exc:
            logger.warning("BrowserProfile unavailable (%s), using basic browser", exc)
            self._browser = Browser(
                headless=True,
                window_size={"width": vp_width, "height": vp_height},
            )

        self.touch()
        return self._browser, True

    async def is_alive(self) -> bool:
        """Check if the browser is still responsive."""
        if not self._browser:
            return False
        try:
            bc = await self._browser._get_browser_context()
            if bc:
                return True
        except Exception as exc:
            logger.debug("Browser alive check failed for user %s: %s", self.user_id, exc)
        return False

    async def cleanup_locks(self) -> None:
        """Remove stale Chrome lock files."""
        for lock_file in CHROME_LOCK_FILES:
            lock_path = self.profile_dir / lock_file
            if lock_path.exists() or lock_path.is_symlink():
                try:
                    lock_path.unlink()
                    logger.info("Removed stale %s from profile %s", lock_file, self.user_id)
                except OSError:
                    pass

    async def kill_orphaned_processes(self) -> None:
        """Kill any orphaned chrome processes using this profile dir."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill",
                "-f",
                f"--user-data-dir={self.profile_dir}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            if proc.returncode == 0:
                logger.info("Killed orphaned chrome for user %s", self.user_id)
                await asyncio.sleep(2)
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            pass

    async def close(self) -> None:
        """Properly close browser, kill chrome, clean lock files."""
        if self._browser:
            try:
                if hasattr(self._browser, "close"):
                    await self._browser.close()
                elif hasattr(self._browser, "stop"):
                    await self._browser.stop()
            except Exception as exc:
                logger.warning("Browser close error: %s", exc)
            self._browser = None

        await self.kill_orphaned_processes()
        await self.cleanup_locks()
        logger.info("Closed browser session for user %s", self.user_id)

    async def _force_cleanup(self) -> None:
        """Force cleanup without graceful close (browser already dead)."""
        self._browser = None
        await self.kill_orphaned_processes()
        await self.cleanup_locks()

    def touch(self) -> None:
        """Update last activity timestamp."""
        self._last_activity = datetime.now(timezone.utc)

    def is_idle(self, timeout: int = SESSION_IDLE_TIMEOUT) -> bool:
        """Check if session has been idle longer than timeout."""
        if not self._last_activity:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_activity).total_seconds()
        return elapsed > timeout


class BrowserSessionPool:
    """Manages browser sessions across all users with idle cleanup."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sessions: dict[str, PersistentBrowserManager] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def get_session(self, user_id: str) -> PersistentBrowserManager:
        """Get or create a browser session for the given user."""
        if user_id not in self._sessions:
            self._sessions[user_id] = PersistentBrowserManager(
                user_id, self._config.database_dir
            )
        return self._sessions[user_id]

    async def close_session(self, user_id: str) -> None:
        """Close a specific user's browser session."""
        session = self._sessions.pop(user_id, None)
        if session:
            await session.close()

    async def start(self) -> None:
        """Start the idle session cleanup loop."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Browser session pool started")

    async def stop(self) -> None:
        """Stop cleanup loop and close all sessions."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        for user_id in list(self._sessions):
            await self.close_session(user_id)
        logger.info("Browser session pool stopped")

    async def _cleanup_loop(self) -> None:
        """Periodically close idle browser sessions."""
        while True:
            await asyncio.sleep(SESSION_CLEANUP_INTERVAL)
            idle_users = [
                uid for uid, s in self._sessions.items() if s.is_idle()
            ]
            for uid in idle_users:
                logger.info("Closing idle browser session for user %s", uid)
                await self.close_session(uid)
