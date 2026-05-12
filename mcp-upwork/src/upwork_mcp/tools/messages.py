"""Messaging tools for Upwork MCP."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from ..browser.client import PROFILE_DIR, get_browser

logger = logging.getLogger(__name__)


# ── Seen-rooms tracking ──────────────────────────────────────────────
# Persists across MCP restarts in the per-user Brave profile dir (so it
# inherits the same user-isolation as the cookies). On first call for
# a fresh profile the file doesn't exist; every conversation is
# `is_new=True`. After that, only NEW room_ids get `is_new=True`.
#
# Why a JSON file rather than the LazyClaw DB:
#   - mcp-upwork is a standalone subprocess and the Apache-2.0 upstream
#     doesn't have DB access — we want to keep our patches minimal.
#   - Per-user isolation is already provided by PROFILE_DIR.
#   - The file is small (a list of room IDs) and writes are infrequent
#     (once per inbox sweep) so locking isn't a concern.

_SEEN_ROOMS_FILENAME = "lazyclaw_seen_rooms.json"


def _seen_rooms_path() -> Path:
    return PROFILE_DIR / _SEEN_ROOMS_FILENAME


def _load_seen_rooms() -> set[str]:
    path = _seen_rooms_path()
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("seen_rooms load failed (%s) — treating as empty", exc)
        return set()
    if isinstance(raw, list):
        return {str(x) for x in raw if x}
    return set()


def _save_seen_rooms(rooms: set[str]) -> None:
    path = _seen_rooms_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(rooms)), encoding="utf-8")
    except OSError as exc:
        logger.debug("seen_rooms save failed (%s) — non-fatal", exc)


class MessagesParams(BaseModel):
    """Parameters for getting messages."""
    room_id: str | None = Field(default=None, description="Specific chat room ID or URL")
    unread_only: bool = Field(default=False, description="Only show unread messages")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum conversations to return")


class SendMessageParams(BaseModel):
    """Parameters for sending a message."""
    room_id: str = Field(description="Chat room ID or URL")
    message: str = Field(description="Message content to send")


async def get_messages(params: MessagesParams) -> list[dict]:
    """Get messages from Upwork inbox.

    The 2026 inbox lives at /ab/messages/rooms/ (not /nx/messages — that path
    returns 404). Empty inbox renders a `[data-test="empty-state"]` placeholder
    inside `[data-test="rooms-panel"]`, which we detect to short-circuit the
    wait instead of timing out.

    Returns a list of conversations with last message, sender info, and unread status.
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    url = "https://www.upwork.com/ab/messages/rooms/"
    if params.unread_only:
        url += "?filter=unread"

    # safe_goto: serialized + Cloudflare-resilient + prefers existing
    # Upwork tab. Fixes the contention bug where get_messages and
    # search_jobs in flight at the same time would knock each other's
    # page handle out.
    page = await browser.safe_goto(url)

    conversations: list[dict] = []

    # Wait for either the rooms panel OR the explicit empty state.
    try:
        await page.wait_for_selector(
            '[data-test="rooms-panel"], [data-test="empty-state"], .rooms-panel-body',
            timeout=10000,
        )
    except Exception:
        pass

    # If the empty state is rendered, there are no conversations — bail early.
    empty_state = await page.query_selector('[data-test="empty-state"]')
    if empty_state:
        return []

    # Extract conversation items. The 2026 layout uses .desktop-layout-room as
    # the row class; older / mobile fallbacks kept for resilience.
    room_els = await page.query_selector_all(
        '.desktop-layout-room, [data-test="room-item"], .room-item, .conversation-item'
    )

    seen_rooms = _load_seen_rooms()
    newly_seen: set[str] = set()

    for el in room_els[:params.limit]:
        try:
            conv = await _extract_conversation(el)
            if not conv:
                continue
            room_id = conv.get("room_id")
            # `is_new` is True for any room we haven't recorded before.
            # Drives the "speak to the founder directly" first-contact
            # offer in upwork_inbox_check. False if we've seen the room
            # in any prior sweep, regardless of whether the latest
            # message is new — that's an "ongoing thread" not a fresh
            # client.
            conv["is_new"] = bool(room_id) and room_id not in seen_rooms
            if room_id:
                newly_seen.add(room_id)
            conversations.append(conv)
        except Exception:
            continue

    if newly_seen:
        _save_seen_rooms(seen_rooms | newly_seen)

    return conversations


async def _extract_conversation(el) -> dict | None:
    """Extract conversation data from element."""
    conv = {}

    # Contact name
    name_el = await el.query_selector('[data-test="contact-name"], .contact-name, .sender-name')
    if name_el:
        conv["contact_name"] = (await name_el.text_content() or "").strip()

    if not conv.get("contact_name"):
        return None

    # Room URL/ID
    room_link = await el.query_selector('a[href*="/messages/"]')
    if room_link:
        href = await room_link.get_attribute("href")
        if href:
            conv["room_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"
            # Extract room ID from URL
            if "/messages/" in href:
                conv["room_id"] = href.split("/messages/")[-1].split("/")[0].split("?")[0]

    # Last message preview
    preview_el = await el.query_selector('[data-test="message-preview"], .preview, .last-message')
    if preview_el:
        conv["last_message"] = (await preview_el.text_content() or "").strip()

    # Timestamp
    time_el = await el.query_selector('[data-test="timestamp"], time, .time')
    if time_el:
        conv["timestamp"] = (await time_el.text_content() or "").strip()

    # Unread indicator
    unread_el = await el.query_selector('[data-test="unread"], .unread-badge, .unread-indicator')
    conv["unread"] = unread_el is not None

    # Related job (if any)
    job_el = await el.query_selector('[data-test="related-job"], .job-title')
    if job_el:
        conv["related_job"] = (await job_el.text_content() or "").strip()

    return conv


async def get_conversation_messages(room_id: str, limit: int = 50) -> dict:
    """Get all messages in a specific conversation.

    Args:
        room_id: The room ID or URL
        limit: Maximum messages to return

    Returns conversation details with full message history.
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    # Build URL — Upwork moved /nx/messages/ → /ab/messages/rooms/ in the 2026 redesign.
    if room_id.startswith("http"):
        url = room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{room_id}"

    page = await browser.safe_goto(url)

    conversation = {"room_id": room_id, "messages": []}

    # Contact name
    contact_el = await page.query_selector('[data-test="contact-name"], .contact-name, h2')
    if contact_el:
        conversation["contact_name"] = (await contact_el.text_content() or "").strip()

    # Related job
    job_el = await page.query_selector('[data-test="related-job"], .job-link')
    if job_el:
        conversation["related_job"] = (await job_el.text_content() or "").strip()

    # Extract messages
    message_els = await page.query_selector_all('[data-test="message"], .message-item, .chat-message')

    for el in message_els[-limit:]:  # Get last N messages
        try:
            msg = await _extract_message(el)
            if msg:
                conversation["messages"].append(msg)
        except Exception:
            continue

    return conversation


async def _extract_message(el) -> dict | None:
    """Extract message data from element."""
    msg = {}

    # Sender
    sender_el = await el.query_selector('[data-test="sender"], .sender, .author')
    if sender_el:
        msg["sender"] = (await sender_el.text_content() or "").strip()

    # Message content
    content_el = await el.query_selector('[data-test="content"], .content, .message-text, p')
    if content_el:
        msg["content"] = (await content_el.text_content() or "").strip()

    if not msg.get("content"):
        return None

    # Timestamp
    time_el = await el.query_selector('[data-test="timestamp"], time, .time')
    if time_el:
        msg["timestamp"] = (await time_el.text_content() or "").strip()

    # Check if it's from me
    me_indicator = await el.query_selector('.my-message, [data-test="my-message"], .sent')
    msg["is_mine"] = me_indicator is not None

    # Attachments
    attachment_els = await el.query_selector_all('[data-test="attachment"], .attachment')
    attachments = []
    for att in attachment_els:
        att_name = await att.text_content()
        if att_name:
            attachments.append(att_name.strip())
    if attachments:
        msg["attachments"] = attachments

    return msg


async def send_message(params: SendMessageParams) -> dict:
    """Send a message in a conversation.

    Args:
        params.room_id: Chat room ID or URL
        params.message: Message content

    Returns send status.
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    # Navigate to conversation — same /ab/messages/rooms/ migration as above.
    if params.room_id.startswith("http"):
        url = params.room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{params.room_id}"

    page = await browser.safe_goto(url)

    # Find message input
    input_el = await page.query_selector('[data-test="message-input"], textarea[name*="message"], .message-input textarea')
    if not input_el:
        return {"status": "error", "message": "Message input not found"}

    # Type message
    await input_el.fill(params.message)

    # Find and click send button
    send_btn = await page.query_selector('[data-test="send-button"], button[type="submit"]:has-text("Send"), button:has-text("Send")')
    if not send_btn:
        # Try pressing Enter
        await input_el.press("Enter")
    else:
        await send_btn.click()

    # Wait for message to appear
    import asyncio
    await asyncio.sleep(2)

    # Verify message was sent by checking if input is cleared
    input_value = await input_el.input_value()
    if not input_value:
        return {"status": "sent", "message": "Message sent successfully"}

    return {"status": "unknown", "message": "Could not confirm message was sent"}


async def get_unread_count() -> dict:
    """Get count of unread messages.

    Returns total unread message count.
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    # Check messages badge in header
    page = await browser.safe_goto("https://www.upwork.com/nx/find-work/")

    unread_el = await page.query_selector('[data-test="messages-badge"], .messages-count, .unread-count')
    if unread_el:
        text = (await unread_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            return {"unread_count": int(numbers[0])}

    return {"unread_count": 0}
