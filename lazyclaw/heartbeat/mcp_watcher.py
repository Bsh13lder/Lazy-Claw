"""MCP-based watcher — polls MCP tools on a schedule for changes.

Used for WhatsApp, Email, and other MCP services that don't use browser.
Zero LLM calls during polling — just MCP tool calls + diff check.
Notifies via Telegram when new data is detected.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_mcp_watcher_context(
    service: str,
    tool_name: str,
    tool_args: dict,
    check_interval: int = 120,
    instruction: str = "",
    expires_at: str | None = None,
    one_shot: bool = False,
    auto_reply: str | None = None,
) -> str:
    """Build a JSON context blob for an MCP watcher job.

    Args:
        service: MCP service name ("whatsapp", "email", etc.)
        tool_name: MCP tool to call ("whatsapp_read", "email_list", etc.)
        tool_args: Arguments for the tool call
        check_interval: Seconds between checks (default 120 = 2min)
        instruction: What to watch for / auto-reply instruction
        expires_at: ISO timestamp for expiration (None = indefinite)
        one_shot: Stop after first change detected
        auto_reply: If set, auto-reply instruction for the agent
    """
    return json.dumps({
        "type": "mcp_watcher",
        "service": service,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "check_interval": check_interval,
        "last_check": 0,
        "last_seen_ids": [],
        "expires_at": expires_at,
        "one_shot": one_shot,
        "instruction": instruction,
        "auto_reply": auto_reply,
    })


def is_mcp_watcher(ctx: dict) -> bool:
    """Check if a watcher context is an MCP watcher."""
    return ctx.get("type") == "mcp_watcher"


def is_mcp_check_due(ctx: dict) -> bool:
    """Check if enough time has passed since last MCP check."""
    interval = float(ctx.get("check_interval", 120))
    last = float(ctx.get("last_check", 0))
    return (time.time() - last) >= interval


def is_mcp_watcher_expired(ctx: dict) -> bool:
    """Check if the MCP watcher has expired."""
    expires = ctx.get("expires_at")
    if not expires:
        return False
    try:
        exp_dt = datetime.fromisoformat(expires)
        return datetime.now(timezone.utc) >= exp_dt
    except (ValueError, TypeError):
        return False


async def check_mcp_watcher(
    ctx: dict,
    mcp_clients: dict,
) -> tuple[bool, str | None, dict]:
    """Run a single MCP watcher check.

    Returns: (changed, notification_text, updated_context)
    - changed: True if new data detected
    - notification_text: Human-readable notification (None if no change)
    - updated_context: New context dict with updated last_check/last_seen_ids
    """
    service = ctx.get("service", "")
    tool_name = ctx.get("tool_name", "")
    tool_args = ctx.get("tool_args", {})
    last_seen = set(ctx.get("last_seen_ids", []))

    # Find the MCP client — match by client name (server_id may be a UUID)
    client = None
    for sid, c in mcp_clients.items():
        client_name = getattr(c, "name", "") or ""
        if service in client_name.lower() or service in sid.lower():
            client = c
            break
    if client is None:
        _names = [getattr(c, "name", sid) for sid, c in mcp_clients.items()]
        logger.warning("MCP watcher: no client for service '%s' (active: %s)", service, _names)
        new_ctx = dict(ctx)
        new_ctx["last_check"] = time.time()
        return False, None, new_ctx

    # Call the MCP tool
    try:
        result = await client.call_tool(tool_name, tool_args)
        raw_text = ""
        for content in result.content:
            if hasattr(content, "text"):
                raw_text += content.text
        data = json.loads(raw_text)
    except Exception as exc:
        logger.warning("MCP watcher call failed (%s): %s", tool_name, exc)
        new_ctx = dict(ctx)
        new_ctx["last_check"] = time.time()
        return False, None, new_ctx

    # Extract messages/items and detect new ones
    new_items = _extract_new_items(data, last_seen, service)

    new_ctx = dict(ctx)
    new_ctx["last_check"] = time.time()

    if not new_items:
        return False, None, new_ctx

    # Update seen IDs (keep last 100 to prevent unbounded growth)
    all_ids = list(last_seen | {item["id"] for item in new_items})
    new_ctx["last_seen_ids"] = all_ids[-100:]

    # Build notification
    notification = _format_notification(new_items, service, ctx.get("instruction", ""))

    return True, notification, new_ctx


def _extract_new_items(
    data: dict | list,
    last_seen: set,
    service: str,
) -> list[dict]:
    """Extract new items from MCP tool response, filtering out already-seen ones."""

    # WhatsApp: {"contact": "...", "messages": [...]}
    if service == "whatsapp":
        messages = []
        if isinstance(data, dict):
            messages = data.get("messages", [])
        elif isinstance(data, list):
            messages = data

        new_msgs = []
        for msg in messages:
            # Skip our own messages
            if msg.get("fromMe", False):
                continue
            # Build unique ID from timestamp + body prefix
            msg_id = f"{msg.get('timestamp', 0)}_{str(msg.get('body', ''))[:20]}"
            if msg_id not in last_seen:
                new_msgs.append({
                    "id": msg_id,
                    "from": msg.get("from", "unknown"),
                    "body": msg.get("body", ""),
                    "timestamp": msg.get("timestamp", 0),
                })
        return new_msgs

    # Email: list of {"subject": ..., "from": ..., "id": ...}
    if service == "email":
        items = data if isinstance(data, list) else data.get("emails", [])
        return [
            {"id": str(item.get("id", i)), **item}
            for i, item in enumerate(items)
            if str(item.get("id", i)) not in last_seen
        ]

    # Instagram: notifications list
    if service == "instagram":
        items = data if isinstance(data, list) else data.get("notifications", [])
        new_items = []
        for i, item in enumerate(items):
            item_id = str(item.get("id", item.get("pk", i)))
            if item_id not in last_seen:
                new_items.append({
                    "id": item_id,
                    "type": item.get("type", "notification"),
                    "user": item.get("user", item.get("from", "?")),
                    "text": item.get("text", item.get("body", "")),
                })
        return new_items

    # Generic: treat as list of objects with "id" field
    items = data if isinstance(data, list) else []
    return [
        {"id": str(item.get("id", i)), **item}
        for i, item in enumerate(items)
        if str(item.get("id", i)) not in last_seen
    ]


def _format_notification(
    items: list[dict],
    service: str,
    instruction: str,
) -> str:
    """Format new items into a Telegram notification."""
    if service == "whatsapp":
        lines = [f"WhatsApp — {len(items)} new message(s):"]
        for msg in items[:5]:  # Cap at 5 to avoid spam
            sender = msg.get("from", "?")
            body = msg.get("body", "")[:100]
            lines.append(f"  {sender}: {body}")
        if len(items) > 5:
            lines.append(f"  ... and {len(items) - 5} more")
        if instruction:
            lines.append(f"\nInstruction: {instruction}")
        return "\n".join(lines)

    if service == "email":
        lines = [f"Email — {len(items)} new:"]
        for item in items[:5]:
            subj = item.get("subject", "no subject")[:60]
            sender = item.get("from", "?")
            lines.append(f"  {sender}: {subj}")
        if len(items) > 5:
            lines.append(f"  ... and {len(items) - 5} more")
        return "\n".join(lines)

    if service == "instagram":
        lines = [f"Instagram — {len(items)} new:"]
        for item in items[:5]:
            user = item.get("user", "?")
            text = item.get("text", "")[:80]
            ntype = item.get("type", "")
            lines.append(f"  {user} ({ntype}): {text}")
        if len(items) > 5:
            lines.append(f"  ... and {len(items) - 5} more")
        if instruction:
            lines.append(f"\nInstruction: {instruction}")
        return "\n".join(lines)

    # Generic
    return f"{service} — {len(items)} new item(s) detected"
