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
    try:
        interval = float(ctx.get("check_interval", 120))
        last = float(ctx.get("last_check", 0))
    except (ValueError, TypeError):
        return True  # Corrupted data — run check to fix it
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

    # Call the MCP tool — client.call_tool() returns a string
    try:
        raw_text = await client.call_tool(tool_name, tool_args)
        data = json.loads(raw_text) if raw_text.strip().startswith(("{", "[")) else {"text": raw_text}
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


def _whatsapp_sender_name(msg: dict) -> str:
    """Extract a human-readable sender name from a WhatsApp message.

    Priority: pushName > name > participant name > cleaned JID.
    Group JIDs (xxxxx@g.us) show as 'Group' with participant name if available.
    """
    jid = msg.get("from", "")
    is_group = jid.endswith("@g.us")

    # Try human names first
    name = msg.get("pushName") or msg.get("name") or msg.get("senderName") or ""
    if name:
        if is_group:
            group_name = msg.get("groupName") or msg.get("subject") or "Group"
            return f"{name} ({group_name})"
        return name

    # For groups, try participant field
    if is_group:
        participant = msg.get("participant", "")
        part_name = msg.get("participantName") or msg.get("participantPushName") or ""
        if part_name:
            group_name = msg.get("groupName") or msg.get("subject") or "Group"
            return f"{part_name} ({group_name})"
        group_name = msg.get("groupName") or msg.get("subject") or ""
        if group_name:
            return group_name
        # Last resort: shorten the JID
        return f"Group {jid.split('@')[0][-6:]}"

    # Individual: extract phone number from JID
    phone = jid.split("@")[0] if "@" in jid else jid
    if len(phone) > 8:
        return f"+{phone}"
    return phone or "?"


def _whatsapp_body_preview(msg: dict) -> str:
    """Get a readable preview of a WhatsApp message body.

    Handles: text, media, stickers, voice notes, locations.
    """
    body = msg.get("body", "")
    if body and body != "[media]":
        return body[:120]

    # Check media type
    msg_type = msg.get("type") or msg.get("messageType") or ""
    has_media = msg.get("hasMedia", False)

    if "image" in msg_type or msg.get("image"):
        caption = msg.get("caption", "")
        return f"[Photo] {caption[:80]}" if caption else "[Photo]"
    if "video" in msg_type or msg.get("video"):
        return "[Video]"
    if "audio" in msg_type or "ptt" in msg_type or msg.get("audio"):
        return "[Voice note]"
    if "sticker" in msg_type or msg.get("sticker"):
        return "[Sticker]"
    if "document" in msg_type or msg.get("document"):
        fname = msg.get("fileName") or msg.get("filename") or ""
        return f"[File: {fname}]" if fname else "[Document]"
    if "location" in msg_type or msg.get("location"):
        return "[Location]"
    if "contact" in msg_type:
        return "[Contact card]"

    if has_media:
        return "[Media]"

    return body[:120] if body else "[Empty message]"


def _format_notification(
    items: list[dict],
    service: str,
    instruction: str,
) -> str:
    """Format new items into a Telegram notification."""
    if service == "whatsapp":
        # Group messages by sender, show contact name + last message
        by_sender: dict[str, list[dict]] = {}
        for msg in items:
            sender = _whatsapp_sender_name(msg)
            by_sender.setdefault(sender, []).append(msg)

        total = len(items)
        senders = list(by_sender.keys())
        sender_names = ", ".join(senders[:3])
        if len(senders) > 3:
            sender_names += f" +{len(senders) - 3}"

        lines = [f"WhatsApp — {total} new from {sender_names}\n"]
        for sender, msgs in list(by_sender.items())[:5]:
            last_msg = _whatsapp_body_preview(msgs[-1])
            count = len(msgs)
            count_label = f" ({count})" if count > 1 else ""
            lines.append(f"{sender}{count_label}: {last_msg}")
        if len(by_sender) > 5:
            lines.append(f"... +{len(by_sender) - 5} more contacts")
        lines.append(f"\n{total} unread total")
        return "\n".join(lines)

    if service == "email":
        lines = [f"Email — {len(items)} new\n"]
        for item in items[:5]:
            sender = item.get("from", "?")
            subj = item.get("subject", "no subject")[:80]
            lines.append(f"{sender}: {subj}")
        if len(items) > 5:
            lines.append(f"... +{len(items) - 5} more")
        lines.append(f"\n{len(items)} unread total")
        return "\n".join(lines)

    if service == "instagram":
        lines = [f"Instagram — {len(items)} new\n"]
        for item in items[:5]:
            user = item.get("user", "?")
            text = item.get("text", "")[:100]
            ntype = item.get("type", "message")
            lines.append(f"{user} ({ntype}): {text}")
        if len(items) > 5:
            lines.append(f"... +{len(items) - 5} more")
        lines.append(f"\n{len(items)} unread total")
        return "\n".join(lines)

    # Generic
    return f"{service} — {len(items)} new item(s) detected"
