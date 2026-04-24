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
                    clicked = await snapshot_mgr.perform_click(backend, arg1)
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
