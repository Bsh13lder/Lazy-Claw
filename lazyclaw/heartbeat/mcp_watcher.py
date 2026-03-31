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
from typing import Any

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
    batch_window: int = 0,
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
        batch_window: Seconds to accumulate messages before notifying (0 = immediate)
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
        "batch_window": batch_window,
        "pending_batch": [],
        "batch_started": 0,
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
    config: Any = None,
    user_id: str = "",
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

    # Auto-reconnect: if client missing but config available, try to reconnect
    if client is None and config and user_id:
        try:
            from lazyclaw.mcp.manager import reconnect_service
            client = await reconnect_service(config, user_id, service)
            if client is not None:
                logger.info("MCP watcher: auto-reconnected '%s'", service)
        except Exception:
            logger.warning("MCP watcher: auto-reconnect failed for '%s'", service, exc_info=True)

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
        _msg_count = 0
        if isinstance(data, dict):
            _msg_count = len(
                data.get("messages") or data.get("most_recent_messages") or []
            )
        logger.info(
            "MCP watcher %s: got %d messages, %d already seen",
            service, _msg_count, len(last_seen),
        )
    except Exception as exc:
        logger.warning("MCP watcher call failed (%s): %s", tool_name, exc)
        new_ctx = dict(ctx)
        new_ctx["last_check"] = time.time()
        return False, None, new_ctx

    # Extract messages/items and detect new ones
    new_items = _extract_new_items(data, last_seen, service)

    # Filter out muted chats — uses WhatsApp's own mute setting automatically
    # Also supports manual muted_groups list as override
    if service == "whatsapp":
        manual_muted = {g.lower() for g in ctx.get("muted_groups", [])}
        new_items = [
            item for item in new_items
            if not (
                # Auto-skip: WhatsApp app has this chat muted
                item.get("muted", False)
                # Manual skip: user used /watch mute GroupName
                or (manual_muted and (
                    item.get("groupName", "").lower() in manual_muted
                    or item.get("chatName", "").lower() in manual_muted
                ))
            )
        ]

    new_ctx = dict(ctx)
    new_ctx["last_check"] = time.time()

    if not new_items:
        # No new items — but check if pending batch should flush
        batch_window = int(ctx.get("batch_window", 0))
        pending = list(ctx.get("pending_batch", []))
        batch_started = float(ctx.get("batch_started", 0))
        if pending and batch_window > 0 and batch_started > 0:
            elapsed = time.time() - batch_started
            if elapsed >= batch_window:
                # Flush the accumulated batch
                new_ctx["pending_batch"] = []
                new_ctx["batch_started"] = 0
                notification = _format_notification(
                    pending, service, ctx.get("instruction", ""),
                )
                logger.info(
                    "MCP watcher %s: flushing batch of %d items (window elapsed)",
                    service, len(pending),
                )
                new_ctx["_notified_items"] = pending
                return True, notification, new_ctx
        logger.debug("MCP watcher %s: no new items", service)
        return False, None, new_ctx

    # Update seen IDs (keep last 200 to prevent unbounded growth)
    all_ids = list(last_seen | {item["id"] for item in new_items})
    new_ctx["last_seen_ids"] = all_ids[-200:]

    # Batching: accumulate messages instead of sending immediately
    batch_window = int(ctx.get("batch_window", 0))
    if batch_window > 0:
        pending = list(ctx.get("pending_batch", []))
        batch_started = float(ctx.get("batch_started", 0))
        pending.extend(new_items)
        if batch_started == 0:
            batch_started = time.time()
        elapsed = time.time() - batch_started
        if elapsed >= batch_window:
            # Window elapsed — flush everything
            new_ctx["pending_batch"] = []
            new_ctx["batch_started"] = 0
            notification = _format_notification(
                pending, service, ctx.get("instruction", ""),
            )
            logger.info(
                "MCP watcher %s: flushing batch of %d items",
                service, len(pending),
            )
            new_ctx["_notified_items"] = pending
            return True, notification, new_ctx
        else:
            # Still accumulating — don't notify yet
            new_ctx["pending_batch"] = pending
            new_ctx["batch_started"] = batch_started
            logger.info(
                "MCP watcher %s: batching %d items (%.0fs / %ds window)",
                service, len(pending), elapsed, batch_window,
            )
            return False, None, new_ctx

    # No batching — notify immediately
    notification = _format_notification(new_items, service, ctx.get("instruction", ""))
    new_ctx["_notified_items"] = new_items
    return True, notification, new_ctx


def _extract_new_items(
    data: dict | list,
    last_seen: set,
    service: str,
) -> list[dict]:
    """Extract new items from MCP tool response, filtering out already-seen ones."""

    # WhatsApp: two response formats depending on whether contact was specified:
    # 1. With contact: {"contact": "...", "messages": [{from, body, time, fromMe, id}]}
    # 2. Without contact (overview): {"chats": [...], "most_recent_messages": [{...}]}
    # Only use actual messages (with Baileys IDs) — NOT the chat overview.
    # Chat overview lists ALL chats with any history, flooding notifications.
    if service == "whatsapp":
        new_msgs: list[dict] = []

        messages: list[dict] = []
        if isinstance(data, dict):
            messages = data.get("messages") or data.get("most_recent_messages") or []
        elif isinstance(data, list):
            messages = data

        for msg in messages:
            if msg.get("fromMe", False):
                continue
            msg_time = msg.get("time") or msg.get("timestamp") or "0"
            msg_body = str(msg.get("body", ""))[:30]
            msg_id = msg.get("id") or f"{msg_time}_{msg_body}"
            if msg_id not in last_seen:
                new_msgs.append({
                    "id": msg_id,
                    "from": msg.get("from", "unknown"),
                    "body": msg.get("body", ""),
                    "timestamp": msg_time,
                    "type": msg.get("type", "direct"),
                    "groupName": msg.get("groupName", ""),
                    "chatName": msg.get("chatName", ""),
                    "participantName": msg.get("participantName", ""),
                    "muted": msg.get("muted", False),
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


def _whatsapp_display_sender(msg: dict) -> str:
    """Format sender for display: 'Alice' for direct, 'Alice (Family Group)' for groups."""
    is_group = msg.get("type") == "group"
    sender = msg.get("from", "?")
    if is_group:
        group_name = msg.get("groupName") or msg.get("chatName") or "Group"
        participant = msg.get("participantName") or sender
        return f"{participant} ({group_name})"
    return sender


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


def _compact_time(raw: str) -> str:
    """Extract HH:MM from a timestamp string like '2026-03-28 14:32:05 UTC'."""
    if not raw or raw == "0":
        return ""
    parts = str(raw).split(" ")
    if len(parts) >= 2 and ":" in parts[1]:
        return parts[1][:5]
    if ":" in str(raw):
        return str(raw)[:5]
    return ""


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
    """Format new items into a clean Telegram notification.

    Design: show the LATEST message prominently, then a compact summary
    of other chats. Not a dump of all unread messages.
    """
    if service == "whatsapp":
        # Group by chat (chatName for groups, sender for direct)
        by_chat: dict[str, list[dict]] = {}
        for msg in items:
            is_group = msg.get("type") == "group"
            chat_key = msg.get("chatName") or msg.get("groupName") or msg.get("from", "?")
            if not is_group:
                chat_key = msg.get("from", "?")
            by_chat.setdefault(chat_key, []).append(msg)

        total = len(items)
        chat_count = len(by_chat)
        lines: list[str] = []

        if total == 1:
            # -- Single message: clean and simple --
            msg = items[0]
            sender = _whatsapp_display_sender(msg)
            body = _whatsapp_body_preview(msg)
            t = _compact_time(msg.get("timestamp", ""))
            time_tag = f"  \u00b7  {t}" if t else ""
            lines.append(f"\U0001f4ac  {sender}{time_tag}")
            lines.append(f"\u2514 {body}")
        else:
            # -- Multiple messages: show each chat with latest msg --
            lines.append(f"\U0001f4ac  WhatsApp  \u00b7  {total} new")
            lines.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
            for chat_name, msgs in list(by_chat.items())[:5]:
                last = msgs[-1]
                body = _whatsapp_body_preview(last)
                t = _compact_time(last.get("timestamp", ""))
                count_tag = f" ({len(msgs)})" if len(msgs) > 1 else ""
                time_tag = f"  {t}" if t else ""
                is_group = last.get("type") == "group"
                if is_group:
                    # Show who sent in the group
                    sender = last.get("participantName") or last.get("from", "?")
                    label = f"\U0001f465 {chat_name}{count_tag}{time_tag}"
                    lines.append(f"\n\u25B8 {label}")
                    lines.append(f"  {sender}: {body[:90]}")
                else:
                    lines.append(f"\n\u25B8 {chat_name}{count_tag}{time_tag}")
                    lines.append(f"  {body[:100]}")
            if chat_count > 5:
                lines.append(f"\n  +{chat_count - 5} more chats")

        return "\n".join(lines)

    if service == "email":
        latest = items[-1]
        sender = latest.get("from", "?")
        subj = latest.get("subject", "no subject")[:80]
        lines = [f"\U0001f4e7  {sender}", f"\u2514 {subj}"]
        if len(items) > 1:
            lines.append(f"\n+{len(items) - 1} more")
        return "\n".join(lines)

    if service == "instagram":
        latest = items[-1]
        user = latest.get("user", "?")
        text = latest.get("text", "")[:100]
        ntype = latest.get("type", "notification")
        lines = [f"\U0001f4f7  {user} ({ntype})", f"\u2514 {text}"]
        if len(items) > 1:
            lines.append(f"\n+{len(items) - 1} more")
        return "\n".join(lines)

    return f"{service} \u2014 {len(items)} new"
