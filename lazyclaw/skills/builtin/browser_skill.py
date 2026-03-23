"""Unified browser skill — single CDP-based tool for all browser interactions.

Actions: read, open, click, type, screenshot, tabs, scroll.
Controls user's real Brave browser via Chrome DevTools Protocol.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re

from lazyclaw.browser.browser_settings import touch_browser_activity
from lazyclaw.browser.page_reader import run_extractor, _detect_page_type
from lazyclaw.runtime.tool_result import Attachment, ToolResult
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Shared CDP backend instance (lazy-initialized, on-demand)
_cdp_backend = None

# ── Shortcut mapping ────────────────────────────────────────────────────

# Services with MCP connectors are EXCLUDED — agent must use MCP tools instead.
# Only services without MCP connectors get browser shortcuts.
_SHORTCUTS = {
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
}


def _query_to_url(query: str) -> str:
    """Convert a target like 'whatsapp' to a URL."""
    q = query.lower().strip()
    if q in _SHORTCUTS:
        return _SHORTCUTS[q]
    if q.startswith("http"):
        return q
    if "." in q:
        return f"https://{q}"
    return ""


# ── CDP backend helpers ─────────────────────────────────────────────────

async def _get_cdp_backend(user_id: str = "default"):
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


async def _get_visible_cdp_backend(user_id: str = "default"):
    """Ensure a VISIBLE Brave is running with CDP and return backend.

    Platform-aware:
    - Server mode (Linux + LAZYCLAW_SERVER_MODE): starts noVNC remote session
    - Desktop (Mac/Linux desktop): opens visible window directly

    Three desktop cases:
    1. Visible browser already on port → reuse (no-op, already on stuck page)
    2. Headless browser on port → kill it, relaunch visible, navigate to stuck URL
    3. Nothing running → launch visible Brave fresh
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
        # Browser already running on CDP — check if headless
        is_headless = await _is_browser_headless(port)
        if not is_headless:
            # Case 1: already visible → just reuse (user can see the stuck page)
            logger.info("Brave already visible on CDP port %d, reusing", port)
            if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
                _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
            return _cdp_backend

        # Case 2: headless → capture URL, kill, relaunch visible
        stuck_url: str | None = None
        if _cdp_backend is not None:
            try:
                stuck_url = await _cdp_backend.current_url()
            except Exception:
                pass

        ws_url = await restart_browser_with_cdp(
            port=port, profile_dir=profile_dir,
            browser_bin=config.browser_executable,
        )
        if not ws_url:
            logger.error("Failed to relaunch visible browser — CDP never responded")
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)

        # Give the window a moment to render before raising it
        await asyncio.sleep(1.0)

        if stuck_url:
            try:
                await _cdp_backend.goto(stuck_url)
                logger.info("Visible browser opened on stuck URL: %s", stuck_url)
            except Exception:
                pass
        return _cdp_backend

    # Case 3: nothing running — launch visible Brave
    chrome_bin = config.browser_executable or "google-chrome"
    import os
    from pathlib import Path as _Path
    os.makedirs(profile_dir, exist_ok=True)
    ext_path = str(_Path(__file__).parent.parent.parent / "browser" / "extension")

    await asyncio.create_subprocess_exec(
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
        f"--load-extension={ext_path}",
        f"--disable-extensions-except={ext_path}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    logger.info("Launched VISIBLE Brave (port=%d, profile=%s)", port, profile_dir)

    for _ in range(20):
        await asyncio.sleep(0.5)
        if await find_chrome_cdp(port):
            break

    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def _is_browser_headless(port: int) -> bool:
    """Check if the browser process on the given CDP port is headless."""
    try:
        # Use "--" to stop pgrep parsing the pattern as flags (starts with --)
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", "--", f"headless.*remote-debugging-port={port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode == 0 and bool(stdout.strip())
    except Exception:
        return False  # Can't tell — assume visible to avoid killing user's browser


async def _raise_browser_window() -> None:
    """Bring the Brave/Chrome window to the foreground.

    macOS: osascript activate
    Linux: wmctrl (common on X11/Wayland desktops)
    Windows: no-op (browser launch already foregrounds)
    """
    import sys

    try:
        if sys.platform == "darwin":
            # Try Brave first, fall back to Chrome
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
            # wmctrl -a raises window by name (works on X11, some Wayland)
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
        pass  # wmctrl not installed — silently skip
    except Exception:
        pass


async def _get_remote_cdp_backend(user_id: str = "default"):
    """Start a noVNC remote session and return a CDPBackend connected to it.

    Used on headless Linux servers. The browser runs on a virtual display
    and is accessible via noVNC in the user's mobile browser.
    """
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

    # Reuse existing remote session if active
    existing = get_active_session(user_id)
    if existing:
        if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
            _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
        return _cdp_backend

    # Capture stuck URL from current headless browser
    stuck_url: str | None = None
    if _cdp_backend is not None:
        try:
            stuck_url = await _cdp_backend.current_url()
        except Exception:
            pass

    # Kill headless browser before starting visible one on virtual display
    try:
        kill_proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", f"--remote-debugging-port={int(port)}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await kill_proc.wait()
        await asyncio.sleep(0.5)
    except Exception:
        pass

    await start_remote_session(
        user_id=user_id,
        cdp_port=port,
        profile_dir=profile_dir,
        browser_bin=config.browser_executable,
        stuck_url=stuck_url,
    )
    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def _stop_remote_session(user_id: str = "default") -> None:
    """Stop remote noVNC session and relaunch headless browser."""
    from lazyclaw.browser.remote_takeover import stop_remote_session

    global _cdp_backend

    await stop_remote_session(user_id)

    # Relaunch headless browser so the agent can continue
    _cdp_backend = None
    backend = await _get_cdp_backend(user_id)
    await backend._ensure_connected()


# ── Unified BrowserSkill ────────────────────────────────────────────────

class BrowserSkill(BaseSkill):
    """Single CDP-based tool for all browser interactions.

    Pure action tool — buy tickets, check in, pay bills, order from Amazon,
    read Gmail, navigate. Controls user's real visible Brave.
    """

    def __init__(self, config=None) -> None:
        self._config = config
        self._snapshot_mgr: SnapshotManager | None = None

    def _get_snapshot_manager(self) -> SnapshotManager:
        """Lazy-init snapshot manager."""
        if self._snapshot_mgr is None:
            from lazyclaw.browser.snapshot import SnapshotManager
            self._snapshot_mgr = SnapshotManager()
        return self._snapshot_mgr

    @property
    def name(self) -> str:
        return "browser"

    @property
    def display_name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control the user's REAL Brave browser. Visible on their screen. "
            "Use for reading pages, navigating, clicking, typing, "
            "taking screenshots, listing tabs, scrolling. Shortcuts: "
            "'twitter', 'facebook', 'linkedin'. "
            "NOT for messaging or email apps — those have dedicated MCP tools."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "open", "click", "type", "press_key", "screenshot", "tabs",
                            "scroll", "close", "snapshot", "hover", "drag", "console_logs", "chain"],
                    "description": (
                        "read: get page CONTENT (text, emails, messages) — no interactive refs. Use to understand what's on the page. "
                        "open: navigate + get content summary AND interactive refs [e1],[e2] — use for first visit to a page. "
                        "snapshot: get interactive element refs [e1],[e2] ONLY — use before clicking/typing. No page content. "
                        "click: click element by ref (e5) or description. Returns fresh refs if page changed. "
                        "type: type text into element. Returns fresh refs if page changed. "
                        "press_key: press a keyboard key (Enter, Escape, Tab, Backspace, ArrowDown). "
                        "screenshot: capture screenshot (ONLY when user asks). "
                        "tabs: list all open tabs. "
                        "scroll: scroll up or down. "
                        "close: close/hide the browser. "
                        "hover: hover over element. "
                        "drag: drag element from source to target. "
                        "console_logs: get browser console output. "
                        "chain: execute multiple steps in one call (e.g. steps=['click e2','wait 1','click e5'])."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "For read/open: URL, shortcut (twitter, facebook, linkedin), or tab query. "
                        "For click/type/hover: CSS selector OR natural description. "
                        "For drag: source CSS selector. "
                        "Leave empty for current tab."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Element ref ID from snapshot (e.g. 'e5'). PREFERRED over target for click/type/hover. "
                        "Use snapshot first to see available refs, then click/type by ref."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type' action only).",
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "For chain action: list of steps to execute sequentially. "
                        "Examples: 'click e5', 'type e2 hello world', 'press_key Enter', "
                        "'wait 2', 'snapshot'. Auto-snapshots after page changes."
                    ),
                },
                "task_hint": {
                    "type": "string",
                    "description": "For snapshot: task description to filter relevant page sections (e.g. 'delete emails').",
                },
                "landmark": {
                    "type": "string",
                    "description": "For snapshot: expand only this section (navigation, main, complementary, etc).",
                },
                "destination": {
                    "type": "string",
                    "description": "Target CSS selector for drag action (drop destination).",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down).",
                },
                "visible": {
                    "type": "boolean",
                    "description": "Force visible browser window. Use when user says 'show me', 'make visible', 'I want to see'.",
                },
            },
            "required": ["action"],
        }

    async def _get_backend(self, user_id: str, tab_context=None, visible: bool = False):
        """Return TabContext if injected, else shared CDPBackend."""
        if tab_context is not None:
            return tab_context
        if visible:
            return await _get_visible_cdp_backend(user_id)
        return await _get_cdp_backend(user_id)

    # Services with MCP connectors — hard block browser usage
    _MCP_SERVICES = {
        "whatsapp": "whatsapp",
        "wa": "whatsapp",
        "web.whatsapp.com": "whatsapp",
        "instagram": "instagram",
        "ig": "instagram",
        "instagram.com": "instagram",
        "gmail": "email",
        "mail": "email",
        "email": "email",
        "mail.google.com": "email",
    }

    async def execute(self, user_id: str, params: dict) -> str | ToolResult:
        # Hard block: redirect MCP-backed services away from browser
        target = (params.get("target") or "").lower().strip()
        for keyword, mcp_name in self._MCP_SERVICES.items():
            if keyword in target:
                return (
                    f"STOP: Do not use browser for this. Use search_tools('{mcp_name}') "
                    f"to find the {mcp_name}_* MCP tools instead. Browser is only for "
                    f"services without MCP connectors, or when user explicitly says 'in browser'."
                )

        # Extract optional TabContext (injected by specialist runner)
        tab_context = params.pop("_tab_context", None)
        # Background tasks should never open visible browser
        self._is_background = params.pop("_background", False)
        action = params.get("action", "")
        touch_browser_activity()

        try:
            if action == "read":
                return await self._action_read(user_id, params, tab_context)
            elif action == "open":
                return await self._action_open(user_id, params, tab_context)
            elif action == "click":
                return await self._action_click(user_id, params, tab_context)
            elif action == "type":
                return await self._action_type(user_id, params, tab_context)
            elif action == "press_key":
                return await self._action_press_key(user_id, params, tab_context)
            elif action == "screenshot":
                return await self._action_screenshot(user_id, params, tab_context)
            elif action == "tabs":
                return await self._action_tabs(user_id, params)
            elif action == "scroll":
                return await self._action_scroll(user_id, params, tab_context)
            elif action == "close":
                return await self._action_close(user_id, params)
            elif action == "snapshot":
                return await self._action_snapshot(user_id, params, tab_context)
            elif action == "hover":
                return await self._action_hover(user_id, params, tab_context)
            elif action == "drag":
                return await self._action_drag(user_id, params, tab_context)
            elif action == "console_logs":
                return await self._action_console_logs(user_id, params, tab_context)
            elif action == "chain":
                return await self._action_chain(user_id, params, tab_context)
            else:
                return f"Unknown action: {action}. Use: read, open, click, type, press_key, screenshot, tabs, scroll, close, snapshot, hover, drag, console_logs, chain"
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.info("browser: CDP connection lost (%s), relaunching", e)
            # Browser died — relaunch headless and retry
            global _cdp_backend
            _cdp_backend = None
            try:
                await _get_cdp_backend(user_id)
                return await self.execute(user_id, params)
            except Exception:
                return await self._auto_connect_and_retry(user_id, params)
        except Exception as e:
            logger.error("browser %s failed: %s", action, e, exc_info=True)
            return f"Error: {e}"

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _format_actionable_elements(elements: list[dict], limit: int = 30) -> str:
        """Format extracted actionable elements into a compact text list."""
        hints = []
        for el in elements[:limit]:
            parts = [el.get("tag", "?")]
            if el.get("ariaLabel"):
                parts.append(f'aria-label="{el["ariaLabel"]}"')
            if el.get("text"):
                parts.append(f'"{el["text"][:50]}"')
            if el.get("placeholder"):
                parts.append(f'placeholder="{el["placeholder"]}"')
            if el.get("name"):
                parts.append(f'name="{el["name"]}"')
            if el.get("type"):
                parts.append(f'type={el["type"]}')
            hints.append("  " + " ".join(parts))
        return "\n".join(hints)

    async def _page_context_summary(
        self, backend, heading: str | None = None, url: str | None = None,
    ) -> str:
        """Take ref-ID snapshot + JS extractor content, return compact summary.

        Gives the LLM both page content AND ref-IDs to act on — no need
        for a separate snapshot call after opening a page.
        """
        # Page content via JS extractor (cheap, site-specific)
        page_data = await run_extractor(backend)
        title = page_data.get("title", "") or await backend.title()
        page_url = url or page_data.get("url", "")
        page_text = page_data.get("text", "")

        parts = [f"{heading or ('Opened: ' + title)}\nURL: {page_url}"]

        if page_text:
            preview = page_text[:1500]
            if len(page_text) > 1500:
                preview += "\n... [truncated]"
            parts.append(f"\n--- Page Content ---\n{preview}")

        # Ref-ID snapshot — so LLM can immediately act with refs
        try:
            mgr = self._get_snapshot_manager()
            snapshot = await mgr.take_snapshot(backend)
            snap_text = mgr.format_snapshot(snapshot)
            parts.append(f"\n{snap_text}")
        except Exception:
            # Fallback to old actionable elements if snapshot fails
            try:
                from lazyclaw.browser.dom_optimizer import DOMOptimizer
                elements = await DOMOptimizer.extract_actionable(backend)
                if elements:
                    parts.append(
                        "\n--- Actionable Elements ---\n"
                        + self._format_actionable_elements(elements)
                    )
            except Exception:
                pass

        return "\n".join(parts)

    async def _element_not_found_hint(self, backend, target: str) -> str:
        """When an element isn't found, return actionable elements so the LLM can self-correct."""
        from lazyclaw.browser.dom_optimizer import DOMOptimizer

        try:
            elements = await DOMOptimizer.extract_actionable(backend)
            if elements:
                hint_text = self._format_actionable_elements(elements, limit=25)
                return (
                    f"Element not found: '{target}'. "
                    f"Here are the interactive elements on the page — pick from these:\n{hint_text}"
                )
        except Exception:
            pass
        return f"Element not found: '{target}'. Use action='snapshot' to see page structure."

    # ── Action handlers ─────────────────────────────────────────────────

    async def _action_read(self, user_id: str, params: dict, tab_context=None) -> str:
        """Read content from current tab or navigate+read a target.

        Connects to existing browser via CDP. If no browser running,
        auto-launches headless (background) using the shared profile
        which has cookies from previous visible sessions.
        """
        backend = await self._get_backend(user_id, tab_context)
        target = (params.get("target") or "").strip()

        if target:
            if tab_context:
                # Specialist mode — navigate the isolated tab directly
                nav_url = _query_to_url(target)
                if nav_url:
                    await backend.goto(nav_url)
                    await asyncio.sleep(3)
                else:
                    return f"Couldn't resolve '{target}' to a URL."
            else:
                # Normal mode — try finding an open tab matching the target
                tab_list = await backend.tabs()
                match = next(
                    (t for t in tab_list
                     if target.lower() in t.title.lower()
                     or target.lower() in t.url.lower()),
                    None,
                )
                if match:
                    await backend.switch_tab(match.id)
                else:
                    nav_url = _query_to_url(target)
                    if nav_url:
                        logger.info("No tab '%s', navigating to %s", target, nav_url)
                        await backend.goto(nav_url)
                        await asyncio.sleep(3)
                    else:
                        return f"No tab found matching '{target}' and couldn't resolve to a URL."

        # Use the JS extractor system for structured content
        result = await run_extractor(backend)
        title = result.get("title", "")
        url = result.get("url", "")
        text = result.get("text", "")[:2000]  # Cap content — specialist calls snapshot for refs
        page_type = result.get("type", "generic")

        summary = f"Tab: {title}\nURL: {url}"
        if page_type == "whatsapp" and result.get("unread_count"):
            summary += f"\nUnread: {result['unread_count']}"
        summary += f"\n\n{text}"

        # Inject site-specific knowledge (learned lessons, login flows, etc.)
        if url and self._config:
            try:
                from lazyclaw.browser.site_memory import recall, format_memories_for_context
                memories = await recall(self._config, user_id, url)
                if memories:
                    summary += "\n\n--- Site Knowledge ---\n" + format_memories_for_context(memories)
            except Exception:
                pass  # Site memory is best-effort

        return summary

    async def _action_open(self, user_id: str, params: dict, tab_context=None) -> str:
        """Open Brave and navigate to target. Headless by default for navigation."""
        force_visible = params.pop("visible", False)
        is_background = getattr(self, "_is_background", False)

        target = (params.get("target") or "").strip()

        # No target = "show me the browser" → visible unless background
        # With target = navigation → headless unless explicitly visible
        visible = force_visible or (not target and not is_background)

        if not target:
            backend = await self._get_backend(user_id, tab_context, visible=visible)
            if visible:
                await _raise_browser_window()
                return "Done — Brave is open on your screen."
            return "Done — browser ready (headless)."

        nav_url = _query_to_url(target)
        if not nav_url:
            return f"Couldn't resolve '{target}' to a URL."

        backend = await self._get_backend(user_id, tab_context, visible=visible)
        if visible:
            await _raise_browser_window()

        if not tab_context:
            # Tab matching only for shortcuts (gmail, whatsapp), NOT full URLs.
            # Full URLs always navigate — they may have different hash/search params.
            is_full_url = target.startswith("http://") or target.startswith("https://")
            if not is_full_url:
                try:
                    tab_list = await backend.tabs()
                    match = next(
                        (t for t in tab_list
                         if target.lower() in t.title.lower()
                         or target.lower() in t.url.lower()
                         or nav_url.split("//")[-1].split("/")[0] in t.url),
                        None,
                    )
                    if match:
                        await backend.switch_tab(match.id)
                        return await self._page_context_summary(
                            backend, f"Switched to: {match.title}", match.url,
                        )
                except Exception:
                    pass

        # Navigate to target (with extra wait after fresh launch)
        await asyncio.sleep(2)
        try:
            await backend.goto(nav_url)
        except TimeoutError:
            # Brave might still be loading — retry once
            await asyncio.sleep(3)
            await backend.goto(nav_url)

        result = await self._page_context_summary(backend, None, nav_url)

        # Inject site-specific knowledge for the navigated domain
        if nav_url and self._config:
            try:
                from lazyclaw.browser.site_memory import recall, format_memories_for_context
                memories = await recall(self._config, user_id, nav_url)
                if memories:
                    result += "\n--- Site Knowledge ---\n" + format_memories_for_context(memories)
            except Exception:
                pass

        return result

    async def _action_click(self, user_id: str, params: dict, tab_context=None) -> str:
        """Click an element by ref ID, CSS selector, or natural description."""
        ref = (params.get("ref") or "").strip()
        target = (params.get("target") or "").strip()

        if not ref and not target:
            return "ref or target required for click. Use ref='e5' from snapshot, or a CSS selector/description."

        backend = await self._get_backend(user_id, tab_context)

        # Ref-ID path (preferred) — DOM click via snapshot manager
        if ref:
            mgr = self._get_snapshot_manager()
            meta = await mgr.get_ref_meta(backend, ref)
            clicked = await mgr.perform_click(backend, ref)
            if clicked:
                await asyncio.sleep(random.uniform(0.2, 0.8))
                name = meta.get("name", ref) if meta else ref
                role = meta.get("role", "") if meta else ""
                confirm = f"Clicked: [{ref}] {role} \"{name}\""
                # Auto-snapshot if page changed — specialist sees updated refs immediately
                if await mgr.is_stale(backend):
                    snapshot = await mgr.take_snapshot(backend)
                    return f"{confirm}\n\n{mgr.format_snapshot(snapshot)}"
                return confirm
            return f"Ref '{ref}' not found or element is gone. Take a new snapshot to get fresh refs."

        # Detect if target is a CSS selector (has CSS-specific chars)
        # or a natural description like "Send button", "Message input"
        is_css = bool(re.search(r'[#\.\[\]>:=~^$*]', target))
        if is_css:
            try:
                await backend.click(target)
                confirm = f"Clicked: {target}"
                mgr = self._get_snapshot_manager()
                if await mgr.is_stale(backend):
                    snapshot = await mgr.take_snapshot(backend)
                    return f"{confirm}\n\n{mgr.format_snapshot(snapshot)}"
                return confirm
            except (ValueError, Exception):
                return await self._element_not_found_hint(backend, target)

        # Natural description — find + DOM click via accessibility tree
        clicked = await backend.click_by_role(target)
        if not clicked:
            # Fallback: try as CSS selector anyway (might be a tag name like "button")
            try:
                await backend.click(target)
                return f"Clicked: {target}"
            except (ValueError, Exception):
                return await self._element_not_found_hint(backend, target)

        await asyncio.sleep(0.5)
        confirm = f"Clicked: {clicked['role']} \"{clicked['name']}\""
        mgr = self._get_snapshot_manager()
        if await mgr.is_stale(backend):
            snapshot = await mgr.take_snapshot(backend)
            return f"{confirm}\n\n{mgr.format_snapshot(snapshot)}"
        return confirm

    async def _action_type(self, user_id: str, params: dict, tab_context=None) -> str:
        """Type text into an element by ref ID, CSS selector, or natural description."""
        ref = (params.get("ref") or "").strip()
        target = (params.get("target") or "").strip()
        text = params.get("text", "")
        if (not ref and not target) or not text:
            return "ref (or target) and text required for type action."

        backend = await self._get_backend(user_id, tab_context)

        # Ref-ID path — focus via snapshot manager, then type
        if ref:
            mgr = self._get_snapshot_manager()
            focused = await mgr.focus_ref(backend, ref)
            if focused:
                conn = await backend._ensure_connected()
                for char in text:
                    await conn.send("Input.dispatchKeyEvent", {
                        "type": "keyDown", "text": char, "key": char,
                    })
                    await conn.send("Input.dispatchKeyEvent", {
                        "type": "keyUp", "key": char,
                    })
                    await asyncio.sleep(random.uniform(0.03, 0.12))
                meta = await mgr.get_ref_meta(backend, ref)
                name = meta.get("name", ref) if meta else ref
                confirm = f"Typed '{text[:30]}' into [{ref}] \"{name}\""
                if await mgr.is_stale(backend):
                    snapshot = await mgr.take_snapshot(backend)
                    return f"{confirm}\n\n{mgr.format_snapshot(snapshot)}"
                return confirm
            return f"Ref '{ref}' not found or couldn't focus. Take a new snapshot."

        # Detect if target is a CSS selector (has CSS-specific chars)
        is_css = bool(re.search(r'[#\.\[\]>:=~^$*]', target))
        if is_css:
            await backend.type_text(target, text)
            confirm = f"Typed '{text[:30]}...' into {target}"
            mgr = self._get_snapshot_manager()
            if await mgr.is_stale(backend):
                snapshot = await mgr.take_snapshot(backend)
                return f"{confirm}\n\n{mgr.format_snapshot(snapshot)}"
            return confirm

        # Natural description — find via accessibility tree, focus, then type
        match = await backend.find_element_by_role(target)
        if not match:
            return f"No element found matching '{target}'. Try a CSS selector."

        conn = await backend._ensure_connected()
        # Click to focus
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": match["x"], "y": match["y"],
            "button": "left", "clickCount": 1,
        })
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": match["x"], "y": match["y"],
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(0.2)
        # Type each character
        for char in text:
            await conn.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": char, "key": char})
            await conn.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": char})
            await asyncio.sleep(0.05)
        confirm = f"Typed '{text[:30]}...' into {match['role']} \"{match['name']}\""
        mgr = self._get_snapshot_manager()
        if await mgr.is_stale(backend):
            snapshot = await mgr.take_snapshot(backend)
            return f"{confirm}\n\n{mgr.format_snapshot(snapshot)}"
        return confirm

    async def _action_screenshot(self, user_id: str, params: dict, tab_context=None) -> ToolResult:
        """Take a screenshot of the current tab."""
        backend = await self._get_backend(user_id, tab_context)
        url = await backend.current_url()
        title = await backend.title()
        ss_bytes = await backend.screenshot()
        return ToolResult(
            text=(
                f"Screenshot of: {title}\nURL: {url}\n"
                f"[{len(ss_bytes)} bytes, {len(ss_bytes) // 1024}KB PNG]"
            ),
            attachments=(
                Attachment(
                    data=ss_bytes,
                    media_type="image/png",
                    filename="screenshot.png",
                ),
            ),
        )

    async def _action_tabs(self, user_id: str, params: dict) -> str:
        """List all open tabs."""
        backend = await _get_cdp_backend(user_id)
        tab_list = await backend.tabs()
        if not tab_list:
            return "No tabs found. Is Brave/Chrome running?"

        lines = [f"Open tabs ({len(tab_list)}):"]
        for i, tab in enumerate(tab_list, 1):
            active = " (active)" if tab.active else ""
            lines.append(f"  {i}. {tab.title}{active}")
            lines.append(f"     {tab.url}")
        return "\n".join(lines)

    async def _action_scroll(self, user_id: str, params: dict, tab_context=None) -> str:
        """Scroll the page up or down."""
        direction = params.get("direction", "down")
        backend = await self._get_backend(user_id, tab_context)
        await backend.scroll(direction)
        return f"Scrolled {direction}"

    async def _action_press_key(self, user_id: str, params: dict, tab_context=None) -> str:
        """Press a keyboard key (Enter, Escape, Tab, etc)."""
        key = (params.get("target") or params.get("text") or "").strip()
        if not key:
            return "Key name required (e.g. Enter, Escape, Tab, Backspace, ArrowDown)."
        backend = await self._get_backend(user_id, tab_context)
        await backend.press_key(key)
        return f"Pressed: {key}"

    async def _action_snapshot(self, user_id: str, params: dict, tab_context=None) -> str:
        """Get ref-ID page snapshot — interactive elements grouped by landmark."""
        target = (params.get("target") or "").strip()
        backend = await self._get_backend(user_id, tab_context)

        # If target provided, navigate first (so snapshot doesn't return empty chrome://newtab)
        if target:
            nav_url = _query_to_url(target)
            if nav_url:
                try:
                    await backend.goto(nav_url)
                    await asyncio.sleep(3)
                except Exception:
                    pass

        mgr = self._get_snapshot_manager()
        snapshot = await mgr.take_snapshot(backend)
        task_hint = params.get("task_hint")
        landmark_filter = params.get("landmark")
        snap_text = mgr.format_snapshot(
            snapshot,
            task_hint=task_hint,
            landmark_filter=landmark_filter,
        )

        return snap_text

    async def _action_hover(self, user_id: str, params: dict, tab_context=None) -> str:
        """Hover over an element."""
        target = (params.get("target") or "").strip()
        if not target:
            return "Target (CSS selector) required for hover."
        backend = await self._get_backend(user_id, tab_context)
        await backend.hover(target)
        return f"Hovering over: {target}"

    async def _action_drag(self, user_id: str, params: dict, tab_context=None) -> str:
        """Drag element from source to destination."""
        source = (params.get("target") or "").strip()
        dest = (params.get("destination") or "").strip()
        if not source or not dest:
            return "Both target (source selector) and destination (target selector) required for drag."
        backend = await self._get_backend(user_id, tab_context)
        await backend.drag_and_drop(source, dest)
        return f"Dragged {source} → {dest}"

    async def _action_console_logs(self, user_id: str, params: dict, tab_context=None) -> str:
        """Get browser console logs."""
        backend = await self._get_backend(user_id, tab_context)
        await backend.inject_console_capture()
        logs = await backend.get_console_logs()
        if not logs:
            return "No console logs captured. Console capture is now active — check again after page interactions."
        lines = []
        for log in logs:
            level = log.get("level", "log").upper()
            text = log.get("text", "")
            lines.append(f"[{level}] {text}")
        return "\n".join(lines)

    async def _action_chain(self, user_id: str, params: dict, tab_context=None) -> str:
        """Execute multiple steps in one call — reduces LLM round-trips."""
        steps = params.get("steps", [])
        if not steps or not isinstance(steps, list):
            return "steps array required for chain action. Example: ['click e2', 'wait 1', 'click e5']"

        backend = await self._get_backend(user_id, tab_context)
        mgr = self._get_snapshot_manager()
        results: list[str] = []
        total = len(steps)

        for i, step_str in enumerate(steps, 1):
            if not isinstance(step_str, str) or not step_str.strip():
                results.append(f"{i}. (empty) → skipped")
                continue

            parts = step_str.strip().split(None, 2)
            cmd = parts[0].lower()
            arg1 = parts[1] if len(parts) > 1 else ""
            arg2 = parts[2] if len(parts) > 2 else ""

            try:
                if cmd == "click" and arg1:
                    # Support both ref IDs (e5) and natural descriptions ("Select all conversations")
                    click_target = arg1 + (" " + arg2 if arg2 else "")
                    is_ref = bool(re.match(r'^e\d+$', arg1))

                    if is_ref:
                        # DOM click via performClick — works on all sites
                        meta = await mgr.get_ref_meta(backend, arg1)
                        clicked = await mgr.perform_click(backend, arg1)
                        if not clicked:
                            results.append(f"{i}. click {arg1} → FAILED (element gone)")
                            break
                        display = f"{meta.get('role', '')} \"{meta.get('name', arg1)}\"" if meta else arg1
                    else:
                        # Natural description click — find + DOM click
                        clicked = await backend.click_by_role(click_target)
                        if not clicked:
                            try:
                                await backend.click(click_target)
                                results.append(f"{i}. click \"{click_target}\"")
                                await asyncio.sleep(random.uniform(0.3, 0.8))
                                continue
                            except Exception:
                                results.append(f"{i}. click \"{click_target}\" → NOT FOUND")
                                break
                        display = f"{clicked['role']} \"{clicked['name']}\""

                    results.append(f"{i}. click {arg1 if is_ref else click_target} → {display}")
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                elif cmd == "type" and arg1 and arg2:
                    focused = await mgr.focus_ref(backend, arg1)
                    if not focused:
                        results.append(f"{i}. type {arg1} → FAILED (can't focus)")
                        break
                    conn = await backend._ensure_connected()
                    for char in arg2:
                        await conn.send("Input.dispatchKeyEvent", {
                            "type": "keyDown", "text": char, "key": char,
                        })
                        await conn.send("Input.dispatchKeyEvent", {
                            "type": "keyUp", "key": char,
                        })
                        await asyncio.sleep(random.uniform(0.03, 0.1))
                    results.append(f"{i}. type {arg1} \"{arg2[:30]}\"")
                    await asyncio.sleep(random.uniform(0.2, 0.5))

                elif cmd == "press_key" and arg1:
                    await backend.press_key(arg1)
                    results.append(f"{i}. press_key {arg1}")
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                elif cmd == "wait":
                    secs = min(float(arg1) if arg1 else 1.0, 10.0)
                    await asyncio.sleep(secs)
                    results.append(f"{i}. wait {secs}s")

                elif cmd == "snapshot":
                    snapshot = await mgr.take_snapshot(backend)
                    task_hint = arg1 if arg1 else None
                    snap_text = mgr.format_snapshot(snapshot, task_hint=task_hint)
                    results.append(f"{i}. snapshot ({snapshot.element_count} elements)")
                    # Append snapshot as the last thing in output
                    return (
                        f"Chain ({len(results)}/{total}):\n"
                        + "\n".join(f"  {r}" for r in results)
                        + f"\n\n{snap_text}"
                    )

                elif cmd == "scroll":
                    direction = arg1 if arg1 in ("up", "down") else "down"
                    await backend.scroll(direction)
                    results.append(f"{i}. scroll {direction}")
                    await asyncio.sleep(0.5)

                else:
                    results.append(f"{i}. {step_str} → unknown command")

            except Exception as e:
                results.append(f"{i}. {step_str} → ERROR: {e}")
                break

        # Auto-snapshot after chain to show result
        succeeded = len(results)
        try:
            snapshot = await mgr.take_snapshot(backend)
            snap_text = mgr.format_snapshot(snapshot)
        except Exception:
            snap_text = "(snapshot failed)"

        return (
            f"Chain ({succeeded}/{total}):\n"
            + "\n".join(f"  {r}" for r in results)
            + f"\n\n{snap_text}"
        )

    async def _action_close(self, user_id: str, params: dict) -> str:
        """Close/hide the browser."""
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.config import load_config

        config = load_config()
        port = getattr(config, "cdp_port", 9222)
        global _cdp_backend

        if not await find_chrome_cdp(port):
            return "Browser is not running."

        try:
            proc = await asyncio.create_subprocess_shell(
                f"ps aux | grep 'remote-debugging-port={port}' | grep -v grep | awk '{{print $2}}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            for pid in stdout.decode().strip().split("\n"):
                pid = pid.strip()
                if pid and pid.isdigit():
                    await asyncio.create_subprocess_exec(
                        "kill", pid,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
            _cdp_backend = None
            return "Browser closed. Cookies saved — next open will restore your sessions."
        except Exception as e:
            return f"Error closing browser: {e}"

    # ── Auto-connect logic ──────────────────────────────────────────────

    async def _auto_connect_and_retry(self, user_id: str, params: dict) -> str:
        """Auto-restart Brave with CDP if approved, then retry."""
        from lazyclaw.browser.browser_settings import get_browser_settings
        from lazyclaw.config import load_config

        config = load_config()
        settings = await get_browser_settings(config, user_id)

        if not settings.get("cdp_approved"):
            return (
                "I need to restart Brave with debugging enabled so I can "
                "read your browser tabs. All your "
                "tabs and logins will be preserved — just a 2-3 second "
                "restart. Say 'yes, connect browser' to allow. I'll "
                "remember your choice for next time."
            )

        from lazyclaw.browser.cdp_backend import restart_browser_with_cdp

        port = getattr(config, "cdp_port", 9222)
        profile_dir = str(config.database_dir / "browser_profiles" / user_id)
        ws_url = await restart_browser_with_cdp(port=port, profile_dir=profile_dir)

        if not ws_url:
            return "Failed to restart browser with debugging. Check if Brave is installed."

        global _cdp_backend
        from lazyclaw.browser.cdp_backend import CDPBackend
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)

        try:
            return await self.execute(user_id, params)
        except Exception as e:
            return f"Browser restarted but action failed: {e}"
