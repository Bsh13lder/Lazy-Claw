"""Browser watcher — zero-token page change detection via CDP JavaScript.

Polls open browser tabs using JS extractors. Known sites (WhatsApp, Gmail)
use built-in extractors. Unknown sites get a one-time LLM-generated JS
snippet stored in the job context.

No LLM calls during polling — pure CDP evaluate(). Only triggers the
agent when a change is detected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from lazyclaw.browser.page_reader import (
    JS_EMAIL,
    JS_WHATSAPP,
    _detect_page_type,
)

logger = logging.getLogger(__name__)

# Built-in extractors for known sites (zero LLM cost)
_BUILTIN_EXTRACTORS: dict[str, str] = {
    "whatsapp": JS_WHATSAPP,
    "email": JS_EMAIL,
}

# Generic extractor — hash page text to detect any change
_JS_GENERIC_HASH = """
(() => {
    const sel = ['main', 'article', '[role="main"]', '.content', '#content', 'body'];
    for (const s of sel) {
        const el = document.querySelector(s);
        if (el && el.innerText.trim().length > 50) {
            return el.innerText.trim().substring(0, 3000);
        }
    }
    return document.body?.innerText?.substring(0, 3000) || '';
})()
"""


def _content_hash(text: str) -> str:
    """SHA-256 hash of text for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_watcher_context(
    url: str,
    custom_js: str | None = None,
    check_interval: int = 300,
    expires_at: str | None = None,
    notify_template: str | None = None,
    one_shot: bool = False,
) -> str:
    """Build the JSON context blob stored encrypted in agent_jobs.context."""
    page_type = _detect_page_type(url)

    ctx = {
        "url": url,
        "page_type": page_type,
        "custom_js": custom_js,
        "check_interval": check_interval,
        "expires_at": expires_at,
        "notify_template": notify_template,
        "one_shot": one_shot,
        "last_value": None,
        "last_check": None,
    }
    return json.dumps(ctx)


async def check_watcher(
    backend,
    context: dict,
) -> tuple[bool, str | None, dict]:
    """Run a single watcher check. Returns (changed, notification, updated_context).

    Zero LLM calls — pure JS execution via CDP.
    """
    url = context.get("url", "")
    page_type = context.get("page_type", "auto")
    custom_js = context.get("custom_js")
    last_value = context.get("last_value")
    notify_template = context.get("notify_template")

    # Pick the right extractor
    if custom_js:
        js_code = custom_js
    elif page_type in _BUILTIN_EXTRACTORS:
        js_code = f"({_BUILTIN_EXTRACTORS[page_type]})()"
    else:
        js_code = _JS_GENERIC_HASH

    # Navigate to URL if not already there
    current_url = await backend.current_url()
    current_host = urlparse(current_url).hostname or ""
    target_host = urlparse(url).hostname or ""

    if target_host and target_host not in current_host:
        # Need to find or navigate to the right tab
        tabs = await backend.tabs()
        target_tab = next(
            (t for t in tabs if target_host in (urlparse(t.url).hostname or "")),
            None,
        )
        if target_tab:
            await backend.switch_tab(target_tab.id)
        else:
            # Tab not open — navigate current tab
            await backend.goto(url)
            # Wait for page load
            import asyncio
            await asyncio.sleep(2)

    # WhatsApp sync wait (short, tab is usually loaded)
    if page_type == "whatsapp":
        import asyncio
        for _ in range(5):
            count = await backend.evaluate(
                "(() => document.querySelectorAll('[role=\"row\"]').length)()"
            )
            if count and count > 0:
                break
            await asyncio.sleep(1)

    # Execute JS extractor
    result = await backend.evaluate(js_code)

    # Normalize result for comparison
    if isinstance(result, dict):
        current_value = json.dumps(result, sort_keys=True)
    else:
        current_value = str(result) if result else ""

    # Update context (immutable — new dict)
    new_context = dict(context)
    new_context["last_value"] = current_value
    new_context["last_check"] = datetime.now(timezone.utc).isoformat()

    # First check — just store baseline, no notification
    if last_value is None:
        return False, None, new_context

    # Compare — for WhatsApp, only trigger on unread count change (not timestamp noise)
    changed = False
    if page_type == "whatsapp" and isinstance(result, dict):
        try:
            old = json.loads(last_value)
            old_unread = old.get("unread_count", 0)
            new_unread = result.get("unread_count", 0)
            old_text = old.get("text", "")[:200]
            new_text = result.get("text", "")[:200]
            # Only trigger if unread count changed or top chat message changed
            changed = new_unread != old_unread or new_text != old_text
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse previous watcher value for WhatsApp comparison", exc_info=True)
            changed = current_value != last_value
    else:
        changed = current_value != last_value

    if not changed:
        return False, None, new_context

    # Build notification with DIFF (what's new, not everything)
    notification = _build_notification(
        context, result, notify_template, last_value,
    )

    return True, notification, new_context


def _build_notification(
    context: dict,
    raw_result,
    template: str | None,
    last_value: str | None = None,
) -> str:
    """Build a human-readable notification showing WHAT changed."""
    url = context.get("url", "")
    page_type = context.get("page_type", "auto")

    if template:
        return template

    # WhatsApp — show only chats with new/changed messages
    if page_type == "whatsapp" and isinstance(raw_result, dict):
        new_text = raw_result.get("text", "")
        old_text = ""
        if last_value:
            try:
                old_data = json.loads(last_value)
                old_text = old_data.get("text", "")
            except (json.JSONDecodeError, TypeError):
                logger.warning("Could not parse previous watcher value as JSON; treating as empty")

        # Find new lines (messages that weren't in the previous check)
        old_lines = set(old_text.split("\n"))
        new_lines = new_text.split("\n")
        diff_lines = [ln for ln in new_lines if ln.strip() and ln not in old_lines]

        if diff_lines:
            return "WhatsApp new:\n" + "\n".join(diff_lines[:10])

        unread = raw_result.get("unread_count", 0)
        if unread:
            # Show first chat with unread
            first_chat = new_text.split("\n\n")[0] if new_text else ""
            return f"WhatsApp: {unread} unread\n{first_chat}"

        return f"WhatsApp update:\n{new_text[:300]}"

    if page_type == "email" and isinstance(raw_result, dict):
        count = raw_result.get("email_count", 0)
        text = raw_result.get("text", "")
        # Show just first email
        first = text.split("\n\n")[0] if text else ""
        return f"Email: {count} messages\n{first}"

    # Generic
    host = urlparse(url).hostname or url
    if isinstance(raw_result, dict):
        return f"Change on {host}: {json.dumps(raw_result)[:500]}"
    return f"Change on {host}: {str(raw_result)[:500]}"


def is_watcher_expired(context: dict) -> bool:
    """Check if a watcher has passed its expiration time."""
    expires_at = context.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        if exp.tzinfo is None:
            from datetime import timezone as tz
            exp = exp.replace(tzinfo=tz.utc)
        return now >= exp
    except (ValueError, TypeError):
        logger.debug("Failed to parse watcher expires_at value", exc_info=True)
        return False


def is_check_due(context: dict) -> bool:
    """Check if enough time has passed since last check."""
    interval = context.get("check_interval", 300)
    last_check = context.get("last_check")
    if not last_check:
        return True
    try:
        last = datetime.fromisoformat(last_check)
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            from datetime import timezone as tz
            last = last.replace(tzinfo=tz.utc)
        return (now - last).total_seconds() >= interval
    except (ValueError, TypeError):
        logger.debug("Failed to parse watcher last_check value, treating as due", exc_info=True)
        return True
