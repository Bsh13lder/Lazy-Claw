"""CDP utility functions — browser restart, URL helpers, JS escaping.

Extracted from cdp_backend.py for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from lazyclaw.browser.cdp import find_chrome_cdp

logger = logging.getLogger(__name__)


async def restart_browser_with_cdp(
    port: int = 9222,
    profile_dir: str | None = None,
    browser_bin: str | None = None,
) -> str | None:
    """Kill running browser and relaunch with CDP enabled (visible).

    Same profile directory -> all tabs, cookies, sessions preserved.
    Returns CDP ws_url or None.
    """
    if not browser_bin:
        from lazyclaw.config import load_config
        config = load_config()
        browser_bin = config.browser_executable

    if not browser_bin:
        logger.warning("No browser binary found")
        return None

    # Kill ALL browser instances (visible + headless)
    browser_name = os.path.basename(browser_bin).lower()
    kill_patterns = [
        f"--remote-debugging-port={port}",
    ]
    if "brave" in browser_name:
        kill_patterns.append("Brave Browser")
    else:
        kill_patterns.append("Google Chrome")

    for pattern in kill_patterns:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", pattern,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            logger.warning("Failed to pkill browser matching %r", pattern, exc_info=True)

    await asyncio.sleep(1.5)

    # Clean stale profile locks
    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)
        for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = os.path.join(profile_dir, lock_file)
            try:
                if os.path.exists(lock_path) or os.path.islink(lock_path):
                    os.unlink(lock_path)
            except OSError:
                logger.warning("Could not remove stale profile lock %s", lock_path, exc_info=True)

    # Relaunch VISIBLE browser with CDP
    from lazyclaw.browser.stealth import STEALTH_LAUNCH_ARGS

    ext_path = str(Path(__file__).parent / "extension")
    cmd = [
        browser_bin,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        *STEALTH_LAUNCH_ARGS,
        f"--load-extension={ext_path}",
        f"--disable-extensions-except={ext_path}",
    ]
    if profile_dir:
        cmd.append(f"--user-data-dir={profile_dir}")

    try:
        await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(
            "Relaunched browser with CDP (port=%d, profile=%s)",
            port, profile_dir,
        )

        for _ in range(20):
            await asyncio.sleep(0.5)
            ws_url = await find_chrome_cdp(port)
            if ws_url:
                return ws_url

        logger.warning("Browser launched but CDP not responding after 10s")
    except Exception as exc:
        logger.error("Failed to relaunch browser: %s", exc)

    return None


def is_same_origin_nav(current_url: str, new_url: str) -> bool:
    """Check if navigation is within the same origin (SPA hash/path change)."""
    if not current_url or not new_url:
        return False
    try:
        cur = urlparse(current_url)
        nxt = urlparse(new_url)
        return (
            cur.scheme == nxt.scheme
            and cur.netloc == nxt.netloc
            and cur.netloc != ""
        )
    except Exception:
        logger.debug("Failed to parse URLs for same-origin check", exc_info=True)
        return False


def js_str(s: str) -> str:
    """Escape a Python string for safe use in JavaScript."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
