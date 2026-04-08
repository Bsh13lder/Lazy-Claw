"""Browser capture actions — screenshot, snapshot, console_logs.

Extracted from browser_skill.py for maintainability.
"""

from __future__ import annotations

import asyncio
import logging

from lazyclaw.runtime.tool_result import Attachment, ToolResult

from .backends import get_backend, query_to_url

logger = logging.getLogger(__name__)


async def action_screenshot(
    user_id: str, params: dict, tab_context,
) -> ToolResult:
    """Take a screenshot of the current tab."""
    backend = await get_backend(user_id, tab_context)
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


async def action_snapshot(
    user_id: str, params: dict, tab_context, snapshot_mgr,
) -> str:
    """Get ref-ID page snapshot — interactive elements grouped by landmark."""
    target = (params.get("target") or "").strip()
    backend = await get_backend(user_id, tab_context)

    if target:
        nav_url = query_to_url(target)
        if nav_url:
            try:
                await backend.goto(nav_url)
                await asyncio.sleep(3)
            except Exception as exc:
                logger.debug("Navigation before snapshot failed: %s", exc)

    snapshot = await snapshot_mgr.take_snapshot(backend)
    task_hint = params.get("task_hint")
    landmark_filter = params.get("landmark")
    return snapshot_mgr.format_snapshot(
        snapshot,
        task_hint=task_hint,
        landmark_filter=landmark_filter,
    )


async def action_console_logs(
    user_id: str, params: dict, tab_context,
) -> str:
    """Get browser console logs."""
    backend = await get_backend(user_id, tab_context)
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
