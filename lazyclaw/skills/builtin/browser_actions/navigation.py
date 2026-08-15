"""Browser navigation actions — tabs, scroll, close, show, chain.

Extracted from browser_skill.py for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re

from lazyclaw.browser.action_errors import (
    RETRY_GIVE_UP,
    RETRY_WAIT,
    ActionError,
    ActionErrorCode,
)
from lazyclaw.browser.browser_settings import touch_browser_activity

from .backends import (
    get_backend,
    get_cdp_backend,
    get_visible_cdp_backend,
    raise_browser_window,
)
from .human_actions import human_click_ref, human_type_text

logger = logging.getLogger(__name__)


async def action_tabs(user_id: str, params: dict) -> str:
    """List all open tabs."""
    backend = await get_cdp_backend(user_id)
    tab_list = await backend.tabs()
    if not tab_list:
        return str(ActionError(
            code=ActionErrorCode.FRAME_DETACHED,
            message="No tabs found.",
            hint="Start the browser with action='open' first, or check Brave/Chrome is running.",
            retry_strategy=RETRY_WAIT,
        ))

    lines = [f"Open tabs ({len(tab_list)}):"]
    for i, tab in enumerate(tab_list, 1):
        active = " (active)" if tab.active else ""
        lines.append(f"  {i}. {tab.title}{active}")
        lines.append(f"     {tab.url}")
    return "\n".join(lines)


# --- Tab housekeeping ----------------------------------------------------
#
# Tabs accumulate in the user's real Brave from target="_blank" links,
# OAuth popups, and bundled MCPs (mcp-upwork) that open their own tabs.
# The agent has no concept of pruning them. sweep_idle_tabs is called
# after every successful navigate and trims the oldest non-active tabs
# back under the user's configured cap. action_close_tab is the explicit
# per-tab close the LLM can invoke when it wants to drop a specific tab.

DEFAULT_MAX_TABS = 8

_PINNED_URL_PREFIXES = (
    "chrome://",
    "chrome-extension://",
    "brave://",
    "devtools://",
    "edge://",
)


def _is_pinned_url(url: str) -> bool:
    """System / extension tabs that must never be auto-closed."""
    if not url:
        return True
    if url == "about:blank":
        return True
    return url.startswith(_PINNED_URL_PREFIXES)


async def _resolve_max_tabs(user_id: str) -> int:
    """Read the user's configured cap, falling back to DEFAULT_MAX_TABS."""
    try:
        from lazyclaw.browser.browser_settings import get_browser_settings
        from lazyclaw.config import load_config

        settings = await get_browser_settings(load_config(), user_id)
        cap = int(settings.get("max_open_tabs", DEFAULT_MAX_TABS))
        # Floor at 2 — anything less would force-close the active tab on
        # the next navigate. Ceiling at 50 to keep the sweep cheap.
        return max(2, min(cap, 50))
    except Exception:
        logger.debug("max_open_tabs lookup failed, using default", exc_info=True)
        return DEFAULT_MAX_TABS


async def sweep_idle_tabs(backend, max_tabs: int) -> int:
    """Close oldest non-active tabs until total is at or under max_tabs.

    Brave's /json endpoint returns tabs in MRU order — the active tab is
    first, the least-recently-used is last. We walk from the tail and
    close until we're back under the cap. Skips: active tab, system
    tabs (chrome://, devtools://, brave://, edge://), and about:blank.

    Returns the number of tabs actually closed. Best-effort — any single
    close failure is logged and the sweep continues.
    """
    try:
        tab_list = await backend.tabs()
    except Exception:
        logger.debug("sweep_idle_tabs: tabs() failed", exc_info=True)
        return 0

    overflow = len(tab_list) - max_tabs
    if overflow <= 0:
        return 0

    # MRU → reverse so the oldest are first.
    candidates = [
        t for t in reversed(tab_list)
        if not t.active and not _is_pinned_url(t.url)
    ]
    to_close = candidates[:overflow]

    closed = 0
    for tab in to_close:
        try:
            await backend.close_tab(tab.id)
            closed += 1
        except Exception:
            logger.debug(
                "sweep_idle_tabs: failed to close tab %s", tab.id, exc_info=True,
            )

    if closed:
        logger.info(
            "sweep_idle_tabs: closed %d tab(s), cap=%d, was %d",
            closed, max_tabs, len(tab_list),
        )
    return closed


async def action_close_tab(user_id: str, params: dict) -> str:
    """Close one browser tab by index, URL substring, or title substring.

    Resolution order: numeric target ('1', '2', ...) → 1-based index
    against the action_tabs listing; otherwise case-insensitive
    substring match against URL first, then title.

    Refuses to close the only remaining tab (would crash CDP) or the
    currently active tab — the agent must switch away first.
    """
    target = (params.get("target") or "").strip()
    if not target:
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="close_tab requires a target.",
            hint="Pass an index (e.g. '2'), URL substring, or title substring.",
            retry_strategy=RETRY_GIVE_UP,
        ))

    backend = await get_cdp_backend(user_id)
    tab_list = await backend.tabs()
    if not tab_list:
        return "No tabs to close."
    if len(tab_list) == 1:
        return (
            "Refusing to close the only open tab — use action='close' to "
            "shut down the browser instead."
        )

    match = None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(tab_list):
            match = tab_list[idx]

    if match is None:
        needle = target.lower()
        match = next(
            (t for t in tab_list
             if needle in (t.url or "").lower()
             or needle in (t.title or "").lower()),
            None,
        )

    if match is None:
        return (
            f"No tab matches '{target}'. Use action='tabs' to list open tabs "
            "and pass an index or substring that appears in the URL or title."
        )
    if match.active:
        return (
            f"'{match.title or match.url}' is the active tab — switch away "
            "first (action='read' on a different target), then close it."
        )

    await backend.close_tab(match.id)
    return f"Closed tab: {match.title or match.url}"


async def action_scroll(user_id: str, params: dict, tab_context) -> str:
    """Scroll the page up or down."""
    direction = params.get("direction", "down")
    backend = await get_backend(user_id, tab_context)
    await backend.scroll(direction)
    return f"Scrolled {direction}"


async def action_close(user_id: str, params: dict) -> str:
    """Close/hide the browser."""
    from lazyclaw.browser.cdp import find_chrome_cdp
    from lazyclaw.config import load_config

    from .backends import reset_backend

    config = load_config()
    port = getattr(config, "cdp_port", 9222)

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
        reset_backend()
        return "Browser closed. Cookies saved — next open will restore your sessions."
    except Exception as e:
        return f"Error closing browser: {e}"


async def action_show(user_id: str) -> str:
    """Make the existing browser visible without killing the session."""
    try:
        await get_visible_cdp_backend(user_id)
        await raise_browser_window()
        backend = await get_cdp_backend(user_id)
        try:
            url = await backend.current_url()
            title = await backend.title()
            return f"Browser is now visible. Showing: {title} ({url})"
        except Exception:
            return "Browser is now visible on your screen."
    except Exception as e:
        return f"Could not make browser visible: {e}"


async def action_chain(
    user_id: str, params: dict, tab_context, snapshot_mgr,
) -> str:
    """Execute multiple steps in one call — reduces LLM round-trips."""
    steps = params.get("steps", [])
    if not steps or not isinstance(steps, list):
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="chain requires a steps array.",
            hint="Example: steps=['click e2', 'wait 1', 'click e5']",
            retry_strategy=RETRY_GIVE_UP,
        ))

    backend = await get_backend(user_id, tab_context)
    results: list[str] = []
    total = len(steps)

    for i, step_str in enumerate(steps, 1):
        if not isinstance(step_str, str) or not step_str.strip():
            results.append(f"{i}. (empty) -> skipped")
            continue

        parts = step_str.strip().split(None, 2)
        cmd = parts[0].lower()
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        try:
            if cmd == "click" and arg1:
                click_target = arg1 + (" " + arg2 if arg2 else "")
                is_ref = bool(re.match(r'^e\d+$', arg1))

                if is_ref:
                    meta = await snapshot_mgr.get_ref_meta(backend, arg1)
                    clicked = await human_click_ref(backend, snapshot_mgr, arg1)
                    if not clicked:
                        results.append(f"{i}. click {arg1} -> FAILED (element gone)")
                        break
                    display = f"{meta.get('role', '')} \"{meta.get('name', arg1)}\"" if meta else arg1
                else:
                    clicked = await backend.click_by_role(click_target)
                    if not clicked:
                        try:
                            await backend.click(click_target)
                            results.append(f"{i}. click \"{click_target}\"")
                            await asyncio.sleep(random.uniform(0.3, 0.8))
                            continue
                        except Exception:
                            results.append(f"{i}. click \"{click_target}\" -> NOT FOUND")
                            break
                    display = f"{clicked['role']} \"{clicked['name']}\""

                results.append(f"{i}. click {arg1 if is_ref else click_target} -> {display}")
                await asyncio.sleep(random.uniform(0.3, 0.8))

            elif cmd == "type" and arg1 and arg2:
                focused = await snapshot_mgr.focus_ref(backend, arg1)
                if not focused:
                    results.append(f"{i}. type {arg1} -> FAILED (can't focus)")
                    break
                await human_type_text(backend, arg2)
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
                snapshot = await snapshot_mgr.take_snapshot(backend)
                task_hint = arg1 if arg1 else None
                snap_text = snapshot_mgr.format_snapshot(snapshot, task_hint=task_hint)
                results.append(f"{i}. snapshot ({snapshot.element_count} elements)")
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
                results.append(f"{i}. {step_str} -> unknown command")

        except Exception as e:
            results.append(f"{i}. {step_str} -> ERROR: {e}")
            break

    # Auto-snapshot after chain
    succeeded = len(results)
    try:
        snapshot = await snapshot_mgr.take_snapshot(backend)
        snap_text = snapshot_mgr.format_snapshot(snapshot)
    except Exception:
        logger.debug("Post-chain snapshot failed", exc_info=True)
        snap_text = "(snapshot failed)"

    return (
        f"Chain ({succeeded}/{total}):\n"
        + "\n".join(f"  {r}" for r in results)
        + f"\n\n{snap_text}"
    )
