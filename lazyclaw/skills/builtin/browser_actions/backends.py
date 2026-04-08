"""CDP backend helpers — lazy singletons for headless, visible, and remote browsers.

Extracted from browser_skill.py to keep the skill class focused on actions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Shared CDP backend instance (lazy-initialized, on-demand)
_cdp_backend = None

# ── Shortcut mapping ────────────────────────────────────────────────────

# Services with MCP connectors are EXCLUDED — agent must use MCP tools instead.
# Only services without MCP connectors get browser shortcuts.
SHORTCUTS = {
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
}


def query_to_url(query: str) -> str:
    """Convert a target like 'twitter' to a URL."""
    q = query.lower().strip()
    if q in SHORTCUTS:
        return SHORTCUTS[q]
    if q.startswith("http"):
        return q
    if "." in q:
        return f"https://{q}"
    return ""


# ── CDP backend helpers ─────────────────────────────────────────────────

async def get_cdp_backend(user_id: str = "default"):
    """Get or create the CDP backend for a user.

    Lazy singleton — recreates if user_id profile changed.
    """
    global _cdp_backend
    from lazyclaw.browser.cdp_backend import CDPBackend
    from lazyclaw.config import load_config

    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)

    if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def get_visible_cdp_backend(user_id: str = "default"):
    """Ensure a VISIBLE browser is running with CDP and return backend.

    Platform-aware:
    - Server mode (Linux + LAZYCLAW_SERVER_MODE): starts noVNC remote session
    - Desktop (Mac/Linux desktop): opens visible window directly

    Three desktop cases:
    1. Visible browser already on port -> reuse
    2. Headless browser on port -> kill it, relaunch visible, navigate to stuck URL
    3. Nothing running -> launch visible browser fresh
    """
    from lazyclaw.browser.remote_takeover import is_server_mode

    if is_server_mode():
        return await _get_remote_cdp_backend(user_id)

    from lazyclaw.browser.cdp import find_chrome_cdp
    from lazyclaw.browser.cdp_backend import CDPBackend, restart_browser_with_cdp
    from lazyclaw.config import load_config

    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)
    global _cdp_backend

    ws_url = await find_chrome_cdp(port)
    if ws_url:
        is_headless = await _is_browser_headless(port)
        if not is_headless:
            # Case 1: already visible -> reuse
            logger.info("Browser already visible on CDP port %d, reusing", port)
            if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
                _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
            return _cdp_backend

        # Case 2: headless -> capture URL, kill, relaunch visible
        stuck_url: str | None = None
        if _cdp_backend is not None:
            try:
                stuck_url = await _cdp_backend.current_url()
            except Exception as exc:
                logger.debug("Failed to get current URL before browser restart: %s", exc)

        ws_url = await restart_browser_with_cdp(
            port=port, profile_dir=profile_dir,
            browser_bin=config.browser_executable,
        )
        if not ws_url:
            logger.error("Failed to relaunch visible browser — CDP never responded")
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)

        await asyncio.sleep(1.0)

        if stuck_url:
            try:
                await _cdp_backend.goto(stuck_url)
                logger.info("Visible browser opened on stuck URL: %s", stuck_url)
            except Exception as exc:
                logger.debug("Failed to restore stuck URL after browser restart: %s", exc)
        return _cdp_backend

    # Case 3: nothing running -> launch visible browser
    chrome_bin = config.browser_executable or "google-chrome"
    os.makedirs(profile_dir, exist_ok=True)
    ext_path = str(Path(__file__).parent.parent.parent.parent / "browser" / "extension")

    from lazyclaw.browser.stealth import STEALTH_LAUNCH_ARGS

    await asyncio.create_subprocess_exec(
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        *STEALTH_LAUNCH_ARGS,
        f"--load-extension={ext_path}",
        f"--disable-extensions-except={ext_path}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    logger.info("Launched VISIBLE browser (port=%d, profile=%s)", port, profile_dir)

    for _ in range(20):
        await asyncio.sleep(0.5)
        if await find_chrome_cdp(port):
            break

    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def _is_browser_headless(port: int) -> bool:
    """Check if the browser process on the given CDP port is headless."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", "--", f"headless.*remote-debugging-port={port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode == 0 and bool(stdout.strip())
    except Exception:
        logger.debug("Headless check failed, assuming visible", exc_info=True)
        return False


async def raise_browser_window() -> None:
    """Bring the browser window to the foreground.

    macOS: osascript activate
    Linux: wmctrl (common on X11/Wayland desktops)
    """
    try:
        if sys.platform == "darwin":
            for app in ("Brave Browser", "Google Chrome"):
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e",
                    f'tell application "{app}" to activate',
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                rc = await proc.wait()
                if rc == 0:
                    return
        elif sys.platform == "linux":
            for name in ("Brave", "Chrome", "Chromium"):
                proc = await asyncio.create_subprocess_exec(
                    "wmctrl", "-a", name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                rc = await proc.wait()
                if rc == 0:
                    return
    except FileNotFoundError:
        logger.debug("wmctrl/osascript not installed, skipping window raise")
    except Exception as exc:
        logger.debug("Window raise failed: %s", exc)


async def _get_remote_cdp_backend(user_id: str = "default"):
    """Start a noVNC remote session and return a CDPBackend connected to it."""
    from lazyclaw.browser.cdp_backend import CDPBackend
    from lazyclaw.browser.remote_takeover import (
        get_active_session,
        start_remote_session,
    )
    from lazyclaw.config import load_config

    global _cdp_backend
    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)

    existing = get_active_session(user_id)
    if existing:
        if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
            _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
        return _cdp_backend

    stuck_url: str | None = None
    if _cdp_backend is not None:
        try:
            stuck_url = await _cdp_backend.current_url()
        except Exception as exc:
            logger.debug("Failed to get current URL before remote session: %s", exc)

    try:
        kill_proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", f"--remote-debugging-port={int(port)}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await kill_proc.wait()
        await asyncio.sleep(0.5)
    except Exception as exc:
        logger.debug("pkill before remote session failed: %s", exc)

    await start_remote_session(
        user_id=user_id,
        cdp_port=port,
        profile_dir=profile_dir,
        browser_bin=config.browser_executable,
        stuck_url=stuck_url,
    )
    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def stop_remote_session(user_id: str = "default") -> None:
    """Stop remote noVNC session and relaunch headless browser."""
    from lazyclaw.browser.remote_takeover import stop_remote_session as _stop

    global _cdp_backend
    await _stop(user_id)
    _cdp_backend = None
    backend = await get_cdp_backend(user_id)
    await backend._ensure_connected()


async def get_backend(user_id: str, tab_context=None, visible: bool = False):
    """Return TabContext if injected, else shared CDPBackend."""
    if tab_context is not None:
        return tab_context
    if visible:
        return await get_visible_cdp_backend(user_id)
    return await get_cdp_backend(user_id)


def reset_backend() -> None:
    """Reset the global backend (e.g. after connection loss)."""
    global _cdp_backend
    _cdp_backend = None
