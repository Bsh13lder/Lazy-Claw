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

# Shared VISIBLE CDP backend instance (lazy-initialized, on-demand). This is
# the user's foreground tab — the one they watch the agent drive.
_cdp_backend = None

# Per-user BACKGROUND CDP backends (2026-06-02). Daemon-originated browser work
# (watcher polls + watcher/cron/reminder brain turns) drives a DEDICATED tab in
# the SAME signed-in Brave so it never steals/blocks the visible tab. Keyed by
# user_id; each is pinned to its own tab via owned_tabs + switch_tab.
_background_backends: dict = {}

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

async def _get_user_backend_pref(user_id: str) -> str:
    """Get user's preferred browser backend ('cdp' or 'browser_use')."""
    try:
        from lazyclaw.browser.browser_settings import get_browser_settings
        from lazyclaw.config import load_config
        config = load_config()
        settings = await get_browser_settings(config, user_id)
        return settings.get("backend", "cdp")
    except Exception:
        return "cdp"


async def get_cdp_backend(user_id: str = "default"):
    """Get or create the browser backend for a user.

    Lazy singleton — recreates if user_id profile changed.
    Respects user's backend preference (cdp vs browser_use).
    """
    global _cdp_backend
    from lazyclaw.config import load_config
    from lazyclaw.browser.profile_resolver import resolve_profile_dir

    config = load_config()
    profile_dir = str(resolve_profile_dir(config, user_id))

    # Check if user wants browser-use backend
    backend_pref = await _get_user_backend_pref(user_id)
    if backend_pref == "browser_use":
        from lazyclaw.browser.browser_use_backend import is_available
        if is_available():
            from lazyclaw.browser.browser_use_backend import BrowserUseBackend
            if _cdp_backend is None or getattr(_cdp_backend, "backend_type", "") != "browser_use":
                _cdp_backend = BrowserUseBackend(headless=True, profile_dir=profile_dir)
            return _cdp_backend
        else:
            logger.warning("browser-use not installed, falling back to CDP backend")

    # Default: raw CDP backend
    from lazyclaw.browser.cdp_backend import CDPBackend
    port = getattr(config, "cdp_port", 9222)

    if _cdp_backend is None or getattr(_cdp_backend, "_profile_dir", None) != profile_dir:
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir, user_id=user_id)
    else:
        # User switched on the shared singleton — late-bind so events route correctly
        try:
            _cdp_backend.set_user_id(user_id)
        except AttributeError:
            pass
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
    from lazyclaw.browser.profile_resolver import resolve_profile_dir
    from lazyclaw.config import load_config

    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(resolve_profile_dir(config, user_id))
    global _cdp_backend

    ws_url = await find_chrome_cdp(port)
    if ws_url:
        is_headless = await _is_browser_headless(port)
        if not is_headless:
            # Case 1: already visible -> reuse
            logger.info("Browser already visible on CDP port %d, reusing", port)
            if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
                _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir, user_id=user_id)
            else:
                try:
                    _cdp_backend.set_user_id(user_id)
                except AttributeError:
                    pass
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
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir, user_id=user_id)

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

    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir, user_id=user_id)
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
    from lazyclaw.browser.profile_resolver import resolve_profile_dir
    from lazyclaw.config import load_config

    global _cdp_backend
    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(resolve_profile_dir(config, user_id))

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

    if not config.browser_executable:
        # Fail fast — start_remote_session would otherwise spin up Xvfb +
        # x11vnc + websockify only to crash inside _start_browser when
        # browser_bin="" hits asyncio.create_subprocess_exec.
        raise RuntimeError(
            "Cannot start remote browser session: no browser binary "
            "configured. Install Brave/Chrome/Chromium or set "
            "BROWSER_EXECUTABLE in the environment."
        )

    await start_remote_session(
        user_id=user_id,
        cdp_port=port,
        profile_dir=profile_dir,
        browser_bin=config.browser_executable,
        stuck_url=stuck_url,
    )
    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir, user_id=user_id)
    return _cdp_backend


async def stop_remote_session(user_id: str = "default") -> None:
    """Stop remote noVNC session and relaunch headless browser."""
    from lazyclaw.browser.remote_takeover import stop_remote_session as _stop

    global _cdp_backend
    await _stop(user_id)
    _cdp_backend = None
    backend = await get_cdp_backend(user_id)
    await backend._ensure_connected()


async def get_background_backend(
    user_id: str, target_key: str = "background", home_url: str | None = None,
):
    """Return a CDPBackend pinned to this user's BACKGROUND tab for *target_key*.

    Daemon-originated browser work drives a dedicated tab in the SAME signed-in
    Brave — never the user's visible tab. The tab is created once and
    re-resolved by target id on later turns (self-heals if it was closed by the
    reaper or a restart). It's registered in :mod:`lazyclaw.browser.owned_tabs`
    so the visible backend EXCLUDES it from MRU and the reaper ANCHORS it.

    ``target_key`` names the owning lane: ``"background"`` (shared bg
    brain-turn tab) or ``f"watch:{job_id}"`` (a watcher's parked poll tab).
    """
    from lazyclaw.browser import owned_tabs
    from lazyclaw.browser.cdp_backend import CDPBackend
    from lazyclaw.browser.profile_resolver import resolve_profile_dir
    from lazyclaw.config import load_config

    config = load_config()
    profile_dir = str(resolve_profile_dir(config, user_id))
    port = getattr(config, "cdp_port", 9222)

    backend = _background_backends.get(user_id)
    if backend is None or getattr(backend, "_profile_dir", None) != profile_dir:
        backend = CDPBackend(port=port, profile_dir=profile_dir, user_id=user_id)
        _background_backends[user_id] = backend
    else:
        try:
            backend.set_user_id(user_id)
        except AttributeError:
            pass

    # Resolve / (re)create the owned tab and pin the backend to it. tabs() lists
    # via HTTP without needing a live connection, so it's a cheap existence
    # check before we decide to reuse or create.
    target_id = owned_tabs.get_owned(user_id, target_key)
    existing_ids: set[str] = set()
    try:
        existing_ids = {t.id for t in await backend.tabs()}
    except Exception:
        logger.debug("bg backend tabs() failed; creating a fresh tab", exc_info=True)

    if target_id and target_id in existing_ids:
        await backend.switch_tab(target_id, focus=False)
    else:
        new_id = await backend.new_tab(home_url or "about:blank")
        await backend.switch_tab(new_id, focus=False)
        owned_tabs.set_owned(user_id, target_key, new_id)
    return backend


async def get_backend(user_id: str, tab_context=None, visible: bool = False):
    """Return TabContext if injected, else the role-appropriate CDPBackend.

    Routing (when no TabContext is injected and not a visible-takeover):
      * background lane (watcher/cron/reminder turn) → ``get_background_backend``
        (a dedicated tab, never the user's visible tab);
      * visible lane (foreground turn) → the shared visible ``get_cdp_backend``.
    """
    if tab_context is not None:
        return tab_context
    if visible:
        return await get_visible_cdp_backend(user_id)
    from lazyclaw.runtime.browser_turn_lock import (
        BACKGROUND_ROLE,
        current_browser_role,
    )
    if current_browser_role() == BACKGROUND_ROLE:
        return await get_background_backend(user_id)
    return await get_cdp_backend(user_id)


def reset_backend() -> None:
    """Reset the global backends (e.g. after connection loss).

    Drops both the visible and all background backend instances. The
    ``owned_tabs`` registry is intentionally left intact — the tabs still exist
    in the browser and ``get_background_backend`` re-resolves them by id
    (self-healing if any were closed).
    """
    global _cdp_backend
    _cdp_backend = None
    _background_backends.clear()
