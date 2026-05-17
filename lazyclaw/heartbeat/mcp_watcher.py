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
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Optional async resolver(jid) -> display name | None. When passed into
# _format_notification it lets the WhatsApp formatter swap raw JIDs like
# ``34611234567@s.whatsapp.net`` for the saved contact name ("Maria").
JidResolver = Callable[[str], Awaitable[str | None]]


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
    """Check if enough time has passed since last MCP check.

    Honours ``snoozed_until`` (ISO timestamp) so the ⏰ inline buttons
    on a watcher push can silence polls for a window without deleting
    the job. Malformed timestamps fall through — they never become a
    "snooze forever" silently.
    """
    snoozed_until = ctx.get("snoozed_until")
    if snoozed_until:
        try:
            snooze = datetime.fromisoformat(snoozed_until)
            if snooze.tzinfo is None:
                snooze = snooze.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < snooze:
                return False
        except (ValueError, TypeError):
            logger.debug(
                "Corrupted snoozed_until on MCP watcher — treating as unset",
                exc_info=True,
            )

    try:
        interval = float(ctx.get("check_interval", 120))
        last = float(ctx.get("last_check", 0))
    except (ValueError, TypeError):
        logger.warning("Corrupted MCP watcher interval/last_check data, forcing check")
        return True
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
        logger.debug("Failed to parse MCP watcher expires_at timestamp", exc_info=True)
        return False


async def _build_whatsapp_jid_resolver(
    config: Any, user_id: str,
) -> JidResolver | None:
    """Build a JID→display-name lookup against the encrypted contact store.

    Loaded once per check (≤200 contacts typical), returns a closure that
    matches phone digits in either direction so e.g.
    ``34611234567@s.whatsapp.net`` resolves to a contact saved as
    ``+34 611 23 45 67``. Returns ``None`` if the store is unavailable —
    formatter will fall back to pushName / formatted phone.
    """
    if not config or not user_id:
        return None
    try:
        from lazyclaw.contacts.store import list_contacts
        contacts = await list_contacts(config, user_id)
    except Exception:
        logger.debug("contact store lookup failed for watcher resolver", exc_info=True)
        return None

    # Pre-compute (digits, name) tuples so the closure stays cheap.
    table: list[tuple[str, str]] = []
    for c in contacts:
        name = (c.name_canonical or "").strip()
        if not name:
            continue
        wa = (c.handles.get("whatsapp_jid") or "") if isinstance(c.handles, dict) else ""
        if wa:
            digits = "".join(ch for ch in wa if ch.isdigit())
            if digits:
                table.append((digits, name))
        for phone in (c.handles.get("phone") or []) if isinstance(c.handles, dict) else []:
            digits = "".join(ch for ch in str(phone) if ch.isdigit())
            if digits:
                table.append((digits, name))

    async def _resolve(jid: str) -> str | None:
        if not jid:
            return None
        jid_digits = "".join(ch for ch in jid if ch.isdigit())
        if not jid_digits:
            return None
        # Prefer exact match, then "jid endswith contact digits" so a
        # stored "611234567" still matches "34611234567@s.whatsapp.net".
        for digits, name in table:
            if digits == jid_digits:
                return name
        for digits, name in table:
            if len(digits) >= 6 and (
                jid_digits.endswith(digits) or digits.endswith(jid_digits)
            ):
                return name
        return None

    return _resolve


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

    # ── First-poll baseline (silent seeding) ────────────────────────
    # When a watcher is first created, last_check == 0 and last_seen_ids
    # is empty. Without this guard, _extract_new_items returns EVERY
    # message in the MCP read window as "new" → Telegram floods with
    # the user's existing unread backlog the moment they start watching.
    # Instead, take a baseline of the current ids, store, and stay quiet.
    # The next real "new" message after this baseline fires normally.
    is_first_poll = float(ctx.get("last_check", 0) or 0) == 0
    if is_first_poll:
        baseline_items = _extract_new_items(data, set(), service)
        baseline_ids = [item["id"] for item in baseline_items if item.get("id")]
        new_ctx = dict(ctx)
        new_ctx["last_check"] = time.time()
        new_ctx["last_seen_ids"] = baseline_ids[-200:]
        new_ctx["_baseline_count"] = len(baseline_ids)
        logger.info(
            "MCP watcher %s: first-poll baseline — seeded %d ids, no notification",
            service, len(baseline_ids),
        )
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

    # Build JID resolver once per check — cheap, contact list is small.
    jid_resolver: JidResolver | None = None
    if service == "whatsapp":
        jid_resolver = await _build_whatsapp_jid_resolver(config, user_id)

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
                notification = await _format_notification(
                    pending, service, ctx.get("instruction", ""),
                    resolver=jid_resolver,
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
            notification = await _format_notification(
                pending, service, ctx.get("instruction", ""),
                resolver=jid_resolver,
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
    notification = await _format_notification(
        new_items, service, ctx.get("instruction", ""),
        resolver=jid_resolver,
    )
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
            # Require a real upstream id (Baileys / MCP shaped). Without
            # one, dedup falls back to "{time}_{body[:30]}" — but the
            # upstream sometimes flips between relative ("2 min ago")
            # and absolute timestamps, and trims emoji from body in
            # different ways, so the same message can hash differently
            # across polls and re-fire. Skipping these is safer than
            # surfacing duplicates; the next poll will pick the message
            # up once it has a stable id assigned by Baileys.
            msg_id = msg.get("id")
            if not msg_id:
                logger.debug(
                    "WhatsApp message without id — skipping (dedup-safe)",
                )
                continue
            if msg_id in last_seen:
                continue
            msg_time = msg.get("time") or msg.get("timestamp") or "0"
            new_msgs.append({
                "id": msg_id,
                "from": msg.get("from", "unknown"),
                "body": msg.get("body", ""),
                "timestamp": msg_time,
                "type": msg.get("type", "direct"),
                "groupName": msg.get("groupName", ""),
                "chatName": msg.get("chatName", ""),
                "participantName": msg.get("participantName", ""),
                "pushName": msg.get("pushName", ""),
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


def _pretty_phone(jid: str) -> str:
    """Render a WhatsApp JID as a readable +country phone string."""
    if not jid:
        return "?"
    phone = jid.split("@")[0] if "@" in jid else jid
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return phone or "?"
    if len(digits) <= 8:
        return f"+{digits}"
    # Light spacing: "+34 611 234 567"
    if len(digits) == 11:
        return f"+{digits[:2]} {digits[2:5]} {digits[5:8]} {digits[8:]}"
    if len(digits) == 12:
        return f"+{digits[:2]} {digits[2:5]} {digits[5:8]} {digits[8:]}"
    return f"+{digits}"


async def _whatsapp_sender(msg: dict, resolver: JidResolver | None) -> str:
    """Human display name for the message sender.

    Priority chain:
      1. Saved contact (resolver) keyed on the sender JID — handles both
         direct chats and per-participant lookup in groups.
      2. pushName from Baileys (often the user's chosen WhatsApp name).
      3. Pre-stored participantName / chatName.
      4. Pretty-printed phone.
    """
    jid = msg.get("from", "")
    is_group = (msg.get("type") == "group") or jid.endswith("@g.us")

    # Group participant lookup — the saved contact is the SENDER inside
    # the group, not the group itself.
    participant_jid = msg.get("participant") or jid
    if resolver:
        try:
            resolved = await resolver(participant_jid if is_group else jid)
            if resolved:
                return resolved
        except Exception:
            logger.debug("jid resolver failed for %s", jid, exc_info=True)

    push = (msg.get("pushName") or msg.get("name") or "").strip()
    if push:
        return push

    if is_group:
        part = (msg.get("participantName") or "").strip()
        if part:
            return part
        return _pretty_phone(participant_jid) if participant_jid else "Member"

    return _pretty_phone(jid)


def _whatsapp_chat_label(msg: dict) -> str:
    """Display label for the CHAT — group name for groups, sender for direct."""
    is_group = msg.get("type") == "group" or msg.get("from", "").endswith("@g.us")
    if is_group:
        return (msg.get("groupName") or msg.get("chatName") or "Group").strip()
    # For direct chats, the "chat" *is* the sender; caller already has it.
    return (msg.get("chatName") or "").strip()


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
        if len(body) > 120:
            return body[:117].rstrip() + "…"
        return body

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


async def _format_notification(
    items: list[dict],
    service: str,
    instruction: str,
    *,
    resolver: JidResolver | None = None,
) -> str:
    """Format new items into a clean Telegram notification.

    Design goals (UX):
      * **One-glance read** - sender + time on the title row, body
        underneath. No JID gibberish ever leaks to the user.
      * **Saved-contact names win** - when the resolver knows the JID
        we show the name the user picked, even for group senders.
      * **Group context** - group chats render as `[Group] Group .. Sender`
        so the user knows who sent and where.
      * **Truncation never mid-emoji** - body capped at ~120 chars with
        a clean ellipsis so message bubbles don't blow up.
    """
    if service == "whatsapp":
        return await _format_whatsapp(items, resolver)

    if service == "email":
        latest = items[-1]
        sender = (latest.get("from") or "?").strip()
        subj = (latest.get("subject") or "no subject")[:80]
        lines = [f"\U0001f4e7  {sender}", f"\u2514 {subj}"]
        if len(items) > 1:
            lines.append(f"\n+{len(items) - 1} more")
        return "\n".join(lines)

    if service == "instagram":
        latest = items[-1]
        user = (latest.get("user") or "?").strip()
        text = (latest.get("text") or "")[:100]
        ntype = (latest.get("type") or "notification").strip()
        lines = [f"\U0001f4f7  {user} ({ntype})", f"\u2514 {text}"]
        if len(items) > 1:
            lines.append(f"\n+{len(items) - 1} more")
        return "\n".join(lines)

    return f"{service} \u2014 {len(items)} new"


async def _format_whatsapp(
    items: list[dict],
    resolver: JidResolver | None,
) -> str:
    """Render a beautiful WhatsApp notification \u2014 single or digest layout."""
    # Group messages by chat (group name for groups, sender JID for direct).
    by_chat: dict[str, list[dict]] = {}
    for msg in items:
        is_group = (
            msg.get("type") == "group"
            or msg.get("from", "").endswith("@g.us")
        )
        if is_group:
            chat_key = msg.get("groupName") or msg.get("chatName") or msg.get("from", "?")
        else:
            chat_key = msg.get("from", "?")
        by_chat.setdefault(chat_key, []).append(msg)

    total = len(items)

    # \u2500\u2500 Single-message layout \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    if total == 1:
        msg = items[0]
        is_group = (
            msg.get("type") == "group" or msg.get("from", "").endswith("@g.us")
        )
        sender = await _whatsapp_sender(msg, resolver)
        body = _whatsapp_body_preview(msg)
        t = _compact_time(msg.get("timestamp", ""))
        time_tag = f"  \u00b7  {t}" if t else ""

        if is_group:
            group = _whatsapp_chat_label(msg) or "Group"
            return (
                f"\U0001f4ac  \U0001f465 {group}{time_tag}\n"
                f"  {sender}:  {body}"
            )
        return (
            f"\U0001f4ac  {sender}{time_tag}\n"
            f"  {body}"
        )

    # \u2500\u2500 Digest layout (multiple messages / multiple chats) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    chat_count = len(by_chat)
    header = (
        f"\U0001f4ac  WhatsApp  \u00b7  {total} new "
        f"in {chat_count} chat{'s' if chat_count != 1 else ''}"
    )
    lines: list[str] = [header, "\u2500" * 22]

    # Sort chats by most-recent message first so the freshest reads at the top.
    def _last_time(chat_msgs: list[dict]) -> str:
        return str(chat_msgs[-1].get("timestamp", ""))

    sorted_chats = sorted(by_chat.items(), key=lambda kv: _last_time(kv[1]), reverse=True)

    for chat_key, msgs in sorted_chats[:5]:
        last = msgs[-1]
        is_group = (
            last.get("type") == "group" or last.get("from", "").endswith("@g.us")
        )
        sender = await _whatsapp_sender(last, resolver)
        body = _whatsapp_body_preview(last)
        t = _compact_time(last.get("timestamp", ""))
        count_tag = f"  \u00b7  {len(msgs)} msgs" if len(msgs) > 1 else ""
        time_tag = f"  \u00b7  {t}" if t else ""

        lines.append("")
        if is_group:
            group = _whatsapp_chat_label(last) or "Group"
            lines.append(f"\u25B8 \U0001f465 {group}{count_tag}{time_tag}")
            lines.append(f"   {sender}:  {body[:110]}")
        else:
            lines.append(f"\u25B8 {sender}{count_tag}{time_tag}")
            lines.append(f"   {body[:120]}")

    if chat_count > 5:
        lines.append("")
        lines.append(f"   +{chat_count - 5} more chat{'s' if chat_count - 5 != 1 else ''}")

    return "\n".join(lines)
