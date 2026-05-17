"""Messaging tools for Upwork MCP."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..browser.client import PROFILE_DIR, _is_nav_noise, get_browser

logger = logging.getLogger(__name__)


# ── External-URL guard for outbound DMs ──────────────────────────────
# Upwork's chat filter blocks every external URL in
# /ab/messages/rooms/. The link either gets stripped or shown to the
# recipient as a "blocked link" placeholder — both outcomes destroy
# trust ("trying to move off-platform") and waste the send. Worse,
# repeated link sends may flag the freelancer's account.
#
# Live incident 2026-05-12: user-dictated reply included
# "github.com/Bsh13lder/Lazy-Claw". Upwork filtered it; client saw
# a broken message. User's NL feedback: "fuck you cant send link on
# upwork its bbloking" + "make rule".
#
# Stored in memory at:
#   ~/.claude/projects/.../memory/feedback_upwork_dm_no_links.md
#
# Detection rules (any match → blocked):
#   1. Scheme present: ``http://`` or ``https://``
#   2. Domain.tld bare token: ``github.com``, ``bit.ly`` etc.
#   3. ``www.<domain>`` even without scheme

_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\bwww\.\S+", re.IGNORECASE),
    re.compile(
        r"\b[a-z0-9][a-z0-9-]*\."
        r"(com|org|net|io|app|dev|co|ai|me|us|uk|de|fr|cn|jp|ru|in|"
        r"info|link|page|so|xyz|tech|cloud|live|tv|ly|to|gg|sh|"
        r"fyi|run|studio)"
        r"(/\S*)?\b",
        re.IGNORECASE,
    ),
    # Bare IPv4 with optional :port — catches links to demo/staging servers
    # some freelancers spin up (e.g. "check 10.0.0.1:8080"). Upwork's filter
    # treats these as off-platform links too.
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b"),
)


def _contains_url(text: str) -> str | None:
    """Return the first URL-like match in ``text`` or None.

    Returns the offending substring so callers can echo it back to the
    user in the error message — much easier to spot than "rejected, no
    URLs allowed".
    """
    if not text:
        return None
    for pat in _URL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


# Product-pitch leak guard. Upwork is a freelance-services marketplace
# — the client buys the freelancer's TIME, not the freelancer's tool.
# Naming "LazyClaw" in a DM/proposal reads as bait-and-switch ("they're
# selling me their software, not building my thing") and tanks the
# conversation. Memory rule:
#   feedback_upwork_no_lazyclaw_product_pitch.md
_PRODUCT_PITCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\blazyclaw\b", re.IGNORECASE),
    re.compile(r"\blazy ?claw\b", re.IGNORECASE),
)


def _contains_product_pitch(text: str) -> str | None:
    """Return the offending brand-name token if the message names
    LazyClaw (the freelancer's own tool). Used to refuse messages that
    would read as a product-sales pitch on a services marketplace."""
    if not text:
        return None
    for pat in _PRODUCT_PITCH_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


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
    draft_only: bool = Field(
        default=False,
        description=(
            "When true, type the message into the Upwork compose box "
            "but DO NOT click Send. Use for human-in-loop review: the "
            "caller types the draft, the user inspects it in their "
            "live Brave tab, then sends manually. Returns status='drafted'."
        ),
    )


class EditMessageParams(BaseModel):
    """Parameters for editing an already-sent Upwork message.

    Upwork allows editing your own messages within ~1 hour of sending.
    Past that window the edit option disappears from the message hover-
    menu. We detect that case and return ``status='expired'`` so the
    caller can fall back to sending a correction.
    """
    room_id: str = Field(description="Chat room ID or URL")
    message_index: int = Field(
        description=(
            "Which of YOUR (is_mine=True) recent messages to edit, "
            "counting from the most recent backwards. 0 = your last "
            "message, 1 = your second-to-last, etc. Clamped to the "
            "Upwork-side edit-window (~1h)."
        ),
        ge=0,
    )
    new_content: str = Field(
        description=(
            "Replacement text for the message. Multi-line content is "
            "typed with Shift+Enter between lines (same Tiptap-safe "
            "path as send_message — never triggers an accidental save)."
        ),
    )
    draft_only: bool = Field(
        default=False,
        description=(
            "When true, open the edit modal + type the replacement "
            "text but DO NOT click Save. User confirms manually. "
            "Returns status='drafted_edit'."
        ),
    )


async def get_messages(params: MessagesParams) -> list[dict]:
    """Get messages from Upwork inbox.

    The 2026 inbox lives at /ab/messages/rooms/ (not /nx/messages — that path
    returns 404). Empty inbox renders a `[data-test="empty-state"]` placeholder
    inside `[data-test="rooms-panel"]`, which we detect to short-circuit the
    wait instead of timing out.

    Returns a list of conversations with last message, sender info, and unread status.
    """
    browser = get_browser()
    try:
        await browser.ensure_logged_in()
    except Exception:
        logger.exception("get_messages: ensure_logged_in failed")
        raise

    url = "https://www.upwork.com/ab/messages/rooms/"
    if params.unread_only:
        url += "?filter=unread"

    # safe_goto: serialized + Cloudflare-resilient + prefers existing
    # Upwork tab. Fixes the contention bug where get_messages and
    # search_jobs in flight at the same time would knock each other's
    # page handle out.
    try:
        page = await browser.safe_goto(url)
    except Exception:
        logger.exception("get_messages: safe_goto to %s failed", url)
        raise

    conversations: list[dict] = []

    # Wait for either the rooms panel OR the explicit empty state.
    # 2026 layout sometimes drops the `[data-test="rooms-panel"]` attribute
    # under heavy SPA re-renders; widening the matcher and bumping the
    # timeout keeps the success path stable without changing the empty
    # short-circuit below.
    try:
        await page.wait_for_selector(
            '[data-test="rooms-panel"], [data-test="empty-state"], '
            '.rooms-panel-body, .desktop-layout-room, [data-test="room-item"]',
            timeout=20000,
        )
    except Exception as exc:
        logger.warning(
            "get_messages: wait_for_selector timed out on %s — current url=%s (%s)",
            url, getattr(page, "url", "?"), exc,
        )

    # If the empty state is rendered, there are no conversations — bail early.
    empty_state = await page.query_selector('[data-test="empty-state"]')
    if empty_state:
        return []

    # Extract conversation items. The 2026 layout uses .desktop-layout-room as
    # the row class; older / mobile fallbacks kept for resilience.
    room_els = await page.query_selector_all(
        '.desktop-layout-room, [data-test="room-item"], .room-item, .conversation-item'
    )
    if not room_els:
        logger.warning(
            "get_messages: 0 room elements matched at %s "
            "(rooms-panel, .desktop-layout-room, [data-test=\"room-item\"] all empty). "
            "Likely 2026 layout drift or CF challenge — surface upstream.",
            getattr(page, "url", "?"),
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
            logger.exception("get_messages: per-row extraction raised")
            continue

    if newly_seen:
        _save_seen_rooms(seen_rooms | newly_seen)

    return conversations


async def _extract_conversation(el) -> dict | None:
    """Extract conversation data from element."""
    conv = {}

    # Contact name. The 2026 layout dropped the legacy
    # `[data-test="contact-name"]` hook — rooms are <div class="desktop-
    # layout-room">, with the name inline alongside the avatar initials,
    # last-message preview, and timestamp. Specific selectors first,
    # then defensive row-text parsing so we never silently drop a real
    # conversation just because Upwork moved the name tag.
    name_el = await el.query_selector(
        '[data-test="contact-name"], [data-test="user-name"], '
        '[data-test="room-contact-name"], '
        '[class*="contact-name"], [class*="contactName"], '
        '.sender-name, .contact-name'
    )
    if name_el:
        conv["contact_name"] = (await name_el.text_content() or "").strip()

    if not conv.get("contact_name"):
        # Row-text fallback. .desktop-layout-room renders as:
        #   "<initials>\n<full name>\n<timestamp>\n<preview>\n..."
        # The full name is the first non-trivial line that isn't a
        # two-letter avatar-initials block, isn't a clock string, and
        # isn't a "More options" / "More room options" menu label.
        row_text = (await el.text_content() or "")
        for line in (s.strip() for s in row_text.splitlines()):
            if not line:
                continue
            low = line.lower()
            if len(line) <= 3:  # avatar initials like "JB"
                continue
            if " ago" in low or "more " in low or "options" in low:
                continue
            # Clock string e.g. "9:20 PM" / "1:10 PM PDT"
            if any(ch.isdigit() for ch in line) and (":" in line or "am" in low or "pm" in low):
                continue
            if 2 < len(line) < 60:
                conv["contact_name"] = line
                break

    # Tiptap / 2026 row layout sometimes duplicates the name in the same
    # node: "James Blue, James Blue" or with stray double-space
    # "James Blue, James  Blue". Normalize whitespace before comparing
    # so the collapse fires regardless of inner spacing.
    cn = (conv.get("contact_name") or "").strip()
    if "," in cn:
        import re as _re_cn
        a, _, b = cn.partition(",")
        a_norm = _re_cn.sub(r"\s+", " ", a).strip().lower()
        b_norm = _re_cn.sub(r"\s+", " ", b).strip().lower()
        if a_norm and a_norm == b_norm:
            conv["contact_name"] = _re_cn.sub(r"\s+", " ", a).strip()

    if not conv.get("contact_name"):
        return None

    # Room URL/ID
    # 2026 layout URL shape:
    #   /ab/messages/rooms/room_<hex>?companyReference=...&sidebar=true
    # Legacy shape:
    #   /nx/messages/<id>
    # The old parser split on ``/messages/`` then took ``.split("/")[0]``,
    # which returned the literal string "rooms" for every 2026 conversation
    # — making every downstream ``get_conversation_messages(room_id)`` call
    # navigate to /ab/messages/rooms/rooms (Upwork 404). Probe specifically
    # for ``room_<hex>`` segments, fall back to legacy parsing only when
    # the new pattern doesn't match.
    import re as _re
    room_link = await el.query_selector('a[href*="/messages/"]')
    if room_link:
        href = await room_link.get_attribute("href")
        if href:
            conv["room_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"
            m = _re.search(r"room_[A-Za-z0-9_-]+", href)
            if m:
                conv["room_id"] = m.group(0)
            elif "/messages/" in href:
                # Legacy fallback. Skip the "rooms" path segment if we hit
                # the new URL layout without finding a room_<hex> match.
                tail = href.split("/messages/", 1)[-1].split("?", 1)[0]
                first = tail.split("/", 1)[0]
                if first in ("rooms", "room"):
                    parts = tail.split("/")
                    if len(parts) > 1 and parts[1]:
                        conv["room_id"] = parts[1]
                else:
                    conv["room_id"] = first

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


async def get_conversation_messages(
    room_id: str,
    limit: int = 50,
    me_name: str | None = None,
) -> dict:
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

    # Give Tiptap + the thread renderer a moment to mount after nav.
    import asyncio as _aio
    await _aio.sleep(2)

    conversation: dict = {"room_id": room_id, "messages": []}

    # Contact name. The 2026 layout dropped [data-test="contact-name"]
    # at the conversation-page level. The header h2 used to give it but
    # now h2 frequently captures sidebar headings ("Schedule a meeting",
    # "Messages"). Walk all visible h2s + page <title> and pick the
    # first non-nav-noise candidate.
    candidate_names: list[str] = []
    for sel in (
        '[data-test="contact-name"], [data-test="user-name"]',
        'main header h1, main header h2',
        'h1', 'h2',
    ):
        for el in await page.query_selector_all(sel):
            t = (await el.text_content() or "").strip()
            if t and not _is_nav_noise(t):
                candidate_names.append(t)
        if candidate_names:
            break
    if candidate_names:
        conversation["contact_name"] = candidate_names[0]

    # Related job
    job_el = await page.query_selector(
        '[data-test="related-job"], .job-link, '
        'a[href*="/jobs/~"]'
    )
    if job_el:
        conversation["related_job"] = (await job_el.text_content() or "").strip()

    # Extract messages. The 2026 layout renders each bubble as
    #     <div data-test="story-container">
    #       <div data-test="story-header">SenderName  10:39 PM</div>
    #       <div data-test="story-message">[body text]</div>
    #     </div>
    # The legacy selectors ([data-test="message"], .message-item) returned
    # 0 on this layout — that's why every get_conversation call today
    # came back with "0 messages". Try the new selector first, fall back
    # to the legacy ones in case Upwork ships yet another layout.
    containers = await page.query_selector_all(
        '[data-test="story-container"]'
    )
    if not containers:
        containers = await page.query_selector_all(
            '[data-test="message"], .message-item, .chat-message'
        )

    # Track sender across consecutive bubbles — Upwork sometimes only
    # renders the header on the FIRST bubble of a run from the same
    # person, leaving subsequent bubbles with body only. Carry forward
    # the last-known sender so each emitted message has author info.
    last_sender: str | None = None
    last_timestamp: str | None = None

    for el in containers[-limit:]:
        try:
            msg = await _extract_message(
                el, last_sender=last_sender, last_timestamp=last_timestamp,
            )
            if msg:
                last_sender = msg.get("sender") or last_sender
                last_timestamp = msg.get("timestamp") or last_timestamp
                # is_mine override: if caller passed me_name (e.g.
                # "Vato Tchipa" from lazyclaw's display_name), match
                # the sender against it. This is the only reliable
                # signal — Upwork's bubble class hints (.outgoing
                # / [class*="self"]) are stale on the 2026 layout.
                if me_name and msg.get("sender"):
                    msg["is_mine"] = _sender_matches(msg["sender"], me_name)
                conversation["messages"].append(msg)
        except Exception as exc:
            logger.warning(
                "_extract_conversation: failed to parse message bubble: %s", exc
            )
            continue

    # contact_name: always prefer the FIRST non-mine sender in the
    # actual thread. The page-header h1/h2 captures UI labels like
    # "Conversation info" / "Schedule a meeting" / "Messages" that
    # Upwork keeps renaming. Message-derived names are the canonical
    # source of truth — the sender field came directly from the bubble
    # header. Only fall back to the page header when zero counterparty
    # messages exist (e.g. brand-new room with no incoming message
    # yet).
    derived_name = None
    for m in conversation["messages"]:
        if not m.get("is_mine") and m.get("sender"):
            derived_name = m["sender"]
            break
    if derived_name:
        conversation["contact_name"] = derived_name
    elif conversation.get("contact_name") and _is_nav_noise(
        conversation["contact_name"]
    ):
        conversation.pop("contact_name", None)

    return conversation


def _sender_matches(sender: str, me_name: str) -> bool:
    """Tolerant match between a bubble's sender label and the caller's
    own display name. Handles common variants:
      "Vato T." vs "Vato Tchipa"  → True (first-name prefix match)
      "Vato Tchipa" vs "Vato Tchipa" → True (exact)
      "James Blue" vs "Vato Tchipa"  → False
    """
    if not sender or not me_name:
        return False
    s = sender.strip().lower().rstrip(".")
    m = me_name.strip().lower().rstrip(".")
    if s == m:
        return True
    # First-name match: "vato" matches "vato tchipa"
    s_first = s.split()[0] if s else ""
    m_first = m.split()[0] if m else ""
    if s_first and s_first == m_first and (len(s_first) >= 3):
        return True
    return False


_TIMESTAMP_RE = re.compile(
    r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?\s*(?:[A-Z]{2,4})?\b"
)


async def _extract_message(
    el,
    *,
    last_sender: str | None = None,
    last_timestamp: str | None = None,
) -> dict | None:
    """Extract message data from a single bubble element.

    Supports the 2026 layout where each bubble is a
    <div data-test="story-container"> with two children:
      [data-test="story-header"]  → "Sender Name\\n10:39 PM"
      [data-test="story-message"] → "[body text]"

    Falls through to the legacy [data-test="content"] / .message-text
    selectors when the modern containers aren't present.

    Carries `last_sender` / `last_timestamp` from the caller because
    Upwork sometimes omits the header on consecutive bubbles from the
    same author — without carry-forward those bubbles emit with no
    sender, which breaks any drafter that needs to know "who said what
    last" to construct a reply.
    """
    msg: dict[str, object] = {}

    # 2026 layout: split header + body explicitly.
    header_el = await el.query_selector('[data-test="story-header"]')
    body_el = await el.query_selector('[data-test="story-message"]')

    if header_el is not None or body_el is not None:
        # Header text shape: "Vato Tchipa\n         10:39 PM"
        header_text = ""
        if header_el is not None:
            header_text = (await header_el.text_content() or "").strip()

        ts_match = _TIMESTAMP_RE.search(header_text)
        if ts_match:
            msg["timestamp"] = ts_match.group(0).strip()
            sender_part = (header_text[: ts_match.start()] +
                           header_text[ts_match.end():]).strip()
            sender_part = sender_part.strip(" \n\t,")
            if sender_part:
                msg["sender"] = sender_part
        elif header_text:
            msg["sender"] = header_text

        if body_el is not None:
            msg["content"] = (await body_el.text_content() or "").strip()
    else:
        # Legacy fallback chain.
        sender_el = await el.query_selector(
            '[data-test="sender"], .sender, .author'
        )
        if sender_el:
            msg["sender"] = (await sender_el.text_content() or "").strip()

        content_el = await el.query_selector(
            '[data-test="content"], .content, .message-text, p'
        )
        if content_el:
            msg["content"] = (await content_el.text_content() or "").strip()

        time_el = await el.query_selector(
            '[data-test="timestamp"], time, .time'
        )
        if time_el:
            msg["timestamp"] = (await time_el.text_content() or "").strip()

    if not msg.get("content"):
        return None

    # Carry-forward author info when this bubble omits the header.
    if not msg.get("sender") and last_sender:
        msg["sender"] = last_sender
    if not msg.get("timestamp") and last_timestamp:
        msg["timestamp"] = last_timestamp

    # is_mine: legacy class hint OR sender-name match.
    me_indicator = await el.query_selector(
        '.my-message, [data-test="my-message"], .sent, '
        '[class*="outgoing"], [class*="self"]'
    )
    msg["is_mine"] = me_indicator is not None

    # Attachments
    attachment_els = await el.query_selector_all(
        '[data-test="attachment"], .attachment'
    )
    attachments: list[str] = []
    for att in attachment_els:
        att_name = await att.text_content()
        if att_name:
            attachments.append(att_name.strip())
    if attachments:
        msg["attachments"] = attachments

    return msg


async def _type_with_soft_breaks(page, text: str) -> None:
    """Type ``text`` into the currently-focused element using ``Shift+Enter``
    for newlines instead of raw ``Enter``.

    Workaround for the 2026-05-16 chunking bug: Upwork's Tiptap rich-text
    editor binds raw ``Enter`` to "Send message". When we typed a
    multi-line draft via ``page.keyboard.type(text)``, every ``\\n`` in
    the text emitted a literal Enter keystroke → the editor sent the
    accumulated buffer as ONE message and started a new line in the now-
    empty editor, repeating per newline. An 11-line draft produced 10
    sends.

    ``Shift+Enter`` is the Tiptap StarterKit hard-break shortcut and
    inserts a ``<br>`` without triggering the submit handler — exactly
    what we want for multi-line freelance messages.

    Empty leading/trailing lines are preserved (the user may intend a
    blank line). Carriage returns (``\\r``) are stripped first so
    Windows-style ``\\r\\n`` doesn't double-break.
    """
    import asyncio as _aio
    # Normalize line endings — Windows / paste-from-Word can carry \r\n.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    for i, line in enumerate(lines):
        if i > 0:
            # Soft line-break — NOT a submit.
            await page.keyboard.press("Shift+Enter")
            # Tiny pause so Tiptap's input handler commits the <br>
            # before we start typing the next line. Without this, some
            # builds of ProseMirror occasionally swallow the first char
            # of the new line.
            await _aio.sleep(0.03)
        if line:
            await page.keyboard.type(line, delay=15)


async def send_message(params: SendMessageParams) -> dict:
    """Send a message in a conversation.

    Args:
        params.room_id: Chat room ID or URL
        params.message: Message content

    Returns send status.
    """
    # Hard guard: refuse any outbound message containing an external
    # URL. Upwork blocks them at the chat-filter level — sending such
    # a message wastes the call AND the message comes through to the
    # client as a "blocked link" placeholder that destroys trust.
    # Caller (brain or NL skill) must rephrase before retrying.
    offending = _contains_url(params.message)
    if offending is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused to send — Upwork DMs block external URLs and "
                f"the message contains {offending!r}. Rephrase without "
                f"the URL (say 'check my portfolio' or 'happy to share "
                f"work samples on request' — client can reach your "
                f"portfolio from your Upwork profile, no link needed)."
            ),
            "offending_token": offending,
        }

    # Hard guard: refuse messages that name LazyClaw — Upwork sells
    # services, naming the freelancer's own tool reads as product-pitch
    # bait-and-switch. Memory rule:
    # feedback_upwork_no_lazyclaw_product_pitch.md
    pitch = _contains_product_pitch(params.message)
    if pitch is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused to send — message names {pitch!r}, which "
                f"reads as a product-sales pitch on Upwork. Describe "
                f"the WORK (Python + Scrapy + CDP-driven browser "
                f"automation, daily logs, human-in-loop) without "
                f"branding it. Reframe and retry."
            ),
            "offending_token": pitch,
        }

    browser = get_browser()
    await browser.ensure_logged_in()

    # Navigate to conversation — same /ab/messages/rooms/ migration as above.
    if params.room_id.startswith("http"):
        url = params.room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{params.room_id}"

    page = await browser.safe_goto(url)

    import asyncio as _aio
    # Give the Tiptap editor a moment to mount + focus after navigation.
    await _aio.sleep(1.5)

    # Find message input. The 2026 layout swapped the legacy <textarea>
    # for a Tiptap/ProseMirror rich-text editor — it's a contenteditable
    # <div role="textbox"> with class "tiptap ProseMirror". The old
    # textarea selectors return nothing on this layout, which is why
    # send_message reported "Message input not found" for every reply
    # attempted today. Try the rich-editor selector FIRST; keep the
    # textarea selectors as fallback in case Upwork ships a third layout.
    input_el = await page.query_selector(
        '[role="textbox"][contenteditable="true"], '
        '.tiptap[contenteditable="true"], '
        '[contenteditable="true"][class*="ProseMirror"], '
        '[data-test="message-input"], '
        'textarea[name*="message"], '
        '.message-input textarea'
    )
    if not input_el:
        return {"status": "error", "message": "Message input not found"}

    # contenteditable can't be .fill()'d like a textarea — fill silently
    # no-ops on a Tiptap node. Use focus + type(): focus puts the cursor
    # in the editor, type() emits the keystrokes Tiptap's input handler
    # subscribes to.
    tag = (await input_el.evaluate("e => e.tagName") or "").lower()
    is_textarea = tag in ("textarea", "input")
    if is_textarea:
        await input_el.fill(params.message)
    else:
        await input_el.click()
        await _aio.sleep(0.2)
        # CRITICAL: Upwork's Tiptap editor treats raw Enter as "Send
        # message" — a multi-line message passed through ``keyboard.type``
        # would fire one send per ``\n``, producing the 11-message spam
        # incident observed 2026-05-16 (one Upwork bubble per bullet).
        # We split on newlines and use Shift+Enter between lines, which
        # Tiptap interprets as a soft line-break instead of a submit.
        # Source: Tiptap's StarterKit hard-break extension binds
        # ``Shift+Enter`` to ``setHardBreak`` while ``Enter`` runs the
        # configured submit handler.
        await _type_with_soft_breaks(page, params.message)

    # Human-in-loop review mode: leave the text in the compose box and
    # return without clicking Send. The user inspects + sends manually
    # from their live Brave tab. We do NOT verify a clear editor in
    # this branch — the message is supposed to remain there.
    if params.draft_only:
        return {
            "status": "drafted",
            "message": (
                "Message typed into Upwork compose box. Review in your "
                "Brave tab, then click Send manually. Nothing was sent."
            ),
            "room_id": params.room_id,
            "char_count": len(params.message),
        }

    # Send button. 2026 layout: <button aria-label="Send message">.
    # Keep the older selectors as fallback.
    send_btn = await page.query_selector(
        '[aria-label="Send message"], '
        '[data-test="send-button"], '
        'button[type="submit"]:has-text("Send"), '
        'button:has-text("Send")'
    )
    if not send_btn:
        # Final fallback: press Enter (works on Tiptap with default config)
        await input_el.press("Enter")
    else:
        await send_btn.click()

    # Wait for the editor to clear (= message accepted + flushed).
    await _aio.sleep(2)

    # Verify by reading the editor's text content back. For textareas
    # use input_value; for contenteditable use text_content.
    if is_textarea:
        residual = await input_el.input_value()
    else:
        residual = (await input_el.text_content() or "").strip()

    if not residual:
        return {"status": "sent", "message": "Message sent successfully"}
    return {
        "status": "unknown",
        "message": f"Could not confirm message was sent (editor still contains text: {residual[:80]!r})",
    }


async def edit_message(params: EditMessageParams) -> dict:
    """Edit one of your already-sent Upwork messages.

    Upwork's 2026 layout allows editing your own messages for ~60
    minutes after send. The UI flow:

      1. Hover over the message bubble → action icons appear on the right
      2. Click the "..." (more) icon → context menu opens
      3. Click "Edit" → inline editor replaces the bubble with the
         message text pre-filled in a Tiptap contenteditable
      4. Modify text → press Enter (Tiptap binds Enter to save in
         edit-mode, NOT to send-new-message) OR click the inline Save
         button if rendered

    Past the 60-min window the "Edit" option is absent from the menu
    — we detect that case and return ``status='expired'`` so the
    caller can fall back to sending a correction message.

    Same Tiptap-safe typing path as :func:`send_message`: the
    replacement text is typed with ``Shift+Enter`` between newlines
    via :func:`_type_with_soft_breaks` (raw Enter would save mid-line).

    URL + product-pitch guards fire BEFORE any browser nav — same as
    send_message. An edit that introduces a URL gets rejected without
    touching the page.
    """
    # Hard guard #1: URLs forbidden (same rationale as send_message)
    offending = _contains_url(params.new_content)
    if offending is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused to edit — Upwork DMs block external URLs and "
                f"the replacement text contains {offending!r}. Rephrase "
                "without the URL."
            ),
            "offending_token": offending,
        }
    # Hard guard #2: no product-pitch (LazyClaw branding)
    pitch = _contains_product_pitch(params.new_content)
    if pitch is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused to edit — replacement text names {pitch!r}, "
                "which reads as a product-sales pitch. Describe the "
                "WORK instead."
            ),
            "offending_token": pitch,
        }

    browser = get_browser()
    await browser.ensure_logged_in()

    if params.room_id.startswith("http"):
        url = params.room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{params.room_id}"

    page = await browser.safe_goto(url)

    import asyncio as _aio
    await _aio.sleep(1.5)

    # Locate my own message bubbles (newest first). Upwork's message
    # list renders mine on the right with a distinguishing class /
    # data-attr — keep selectors permissive to survive layout drift.
    my_bubbles = await page.query_selector_all(
        '[data-test="story-container"][data-mine="true"], '
        '[data-test="story-container"].story-mine, '
        '[data-test="own-message"], '
        '.message-row.is-own, '
        '[data-test="message-bubble-self"]'
    )

    # Fallback when no positive selector for self-side hits: read every
    # bubble and assume the most recent N visually-right-aligned ones
    # are mine. Skip this for now — better to fail explicitly than to
    # edit someone else's message by accident. The brain re-tries with
    # a corrected selector if needed.
    if not my_bubbles:
        return {
            "status": "error",
            "message": (
                "Could not locate any of your own message bubbles in "
                "this room. Upwork layout may have changed — verify "
                "with get_conversation_messages first to confirm "
                "is_mine flags are correct."
            ),
        }

    if params.message_index >= len(my_bubbles):
        return {
            "status": "error",
            "message": (
                f"message_index={params.message_index} but only "
                f"{len(my_bubbles)} of your messages are visible. "
                "Lower the index or load more history."
            ),
        }

    # Newest-first ordering: my_bubbles[0] is the BOTTOM bubble (oldest
    # in DOM, but the last sent). Upwork conventionally orders top→bottom
    # = oldest→newest, so we reverse to get newest first.
    target_bubble = list(reversed(my_bubbles))[params.message_index]

    # Hover to reveal action icons. CDP `hover` triggers the same
    # mouseover events Upwork's UI listens for.
    try:
        await target_bubble.hover()
        await _aio.sleep(0.3)
    except Exception as exc:
        logger.warning("hover on target bubble failed: %s", exc)

    # Find the "more actions" / kebab icon. Upwork has used several
    # data-test values for this — try the most common patterns first.
    more_btn = await target_bubble.query_selector(
        '[data-test="message-actions-menu"], '
        '[aria-label*="More" i], '
        '[aria-label*="actions" i], '
        'button[class*="kebab" i], '
        'button[class*="more" i]'
    )
    if not more_btn:
        return {
            "status": "error",
            "message": (
                "Could not find the message actions menu on the target "
                "bubble. Upwork may have rearranged hover-icons. "
                "Inspect the DOM and add the new selector to "
                "edit_message's more_btn query."
            ),
        }
    await more_btn.click()
    await _aio.sleep(0.4)

    # Find the Edit menu item. If absent → past the 60-min edit window.
    edit_item = await page.query_selector(
        '[role="menuitem"]:has-text("Edit"), '
        '[data-test="menu-item-edit"], '
        'button:has-text("Edit"):not(:has-text("Editor"))'
    )
    if not edit_item:
        return {
            "status": "expired",
            "message": (
                "Edit option not in the menu — your 60-minute edit "
                "window has likely passed. Send a new correction "
                "message via send_message instead."
            ),
        }
    await edit_item.click()
    await _aio.sleep(0.5)

    # The bubble now becomes an inline contenteditable Tiptap editor
    # with the original text pre-filled. Selector pattern mirrors the
    # compose-box one in send_message.
    edit_el = await page.query_selector(
        '[role="textbox"][contenteditable="true"][data-test*="edit" i], '
        '[role="textbox"][contenteditable="true"]:focus, '
        '.tiptap[contenteditable="true"][data-edit-message], '
        '.tiptap[contenteditable="true"]:focus'
    )
    if not edit_el:
        # Fall back to any focused contenteditable on the page
        edit_el = await page.query_selector(
            '[contenteditable="true"]:focus'
        )
    if not edit_el:
        return {
            "status": "error",
            "message": (
                "Edit modal opened but couldn't locate the inline editor "
                "element. Aborting before typing to avoid leaking text "
                "into the wrong field."
            ),
        }

    # Clear existing text — select all + delete is safer than
    # programmatic .fill() on contenteditable.
    await edit_el.click()
    await _aio.sleep(0.15)
    await page.keyboard.press("Control+A")
    await _aio.sleep(0.1)
    await page.keyboard.press("Delete")
    await _aio.sleep(0.15)

    # Type replacement via the same Tiptap-safe helper — Shift+Enter
    # between newlines, never a raw Enter.
    await _type_with_soft_breaks(page, params.new_content)

    if params.draft_only:
        return {
            "status": "drafted_edit",
            "message": (
                "Edit pane open with replacement text typed. Review in "
                "your Brave tab, then click Save manually."
            ),
            "room_id": params.room_id,
            "message_index": params.message_index,
            "char_count": len(params.new_content),
        }

    # Save: Tiptap in edit-mode usually binds Enter to save (the inverse
    # of compose-mode's send-on-Enter). Try the explicit Save button
    # first; fall back to a single Enter press if absent.
    save_btn = await page.query_selector(
        '[data-test="save-edit"], '
        '[aria-label="Save edit"], '
        'button:has-text("Save"):not(:has-text("Saved"))'
    )
    if save_btn:
        await save_btn.click()
    else:
        await page.keyboard.press("Enter")

    await _aio.sleep(1.5)

    # Verify by re-querying the bubble and reading its text content.
    # Stale-reference resilient — re-fetch instead of reusing target_bubble.
    refreshed = await page.query_selector_all(
        '[data-test="story-container"][data-mine="true"], '
        '[data-test="story-container"].story-mine, '
        '[data-test="own-message"]'
    )
    if not refreshed or params.message_index >= len(refreshed):
        return {
            "status": "unknown",
            "message": (
                "Edit submitted but could not re-verify the bubble. "
                "Check Upwork manually to confirm the change landed."
            ),
        }
    new_text = (await list(reversed(refreshed))[params.message_index].text_content() or "").strip()
    # Compare normalized — collapse whitespace, ignore trailing chrome
    expected = params.new_content.strip()
    if expected and expected.split() == new_text.split()[:len(expected.split())]:
        return {
            "status": "edited",
            "message": "Message edited successfully.",
            "room_id": params.room_id,
            "message_index": params.message_index,
            "new_content_preview": expected[:120],
        }
    return {
        "status": "unknown",
        "message": (
            f"Edit submitted but bubble text doesn't match expected. "
            f"Bubble shows: {new_text[:120]!r}. Verify manually."
        ),
    }


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
