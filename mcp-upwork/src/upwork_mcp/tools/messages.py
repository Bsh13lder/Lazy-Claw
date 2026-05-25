"""Messaging tools for Upwork MCP."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..browser.client import (
    PROFILE_DIR,
    UpworkRoomNotFound,
    _is_nav_noise,
    get_browser,
)

logger = logging.getLogger(__name__)


# JS snippet that finds the chat scroll container on Upwork's 2026 DOM.
# Three strategies, tried in order:
#
# 1. Class-based selectors (cheap fast path). Updated 2026-05-24 to
#    include ``.scroll-wrapper.custom-scrollbar`` — the actual container
#    Upwork ships today. Without this, every selector below was NULL
#    and scroll-to-bottom became a silent no-op: chat stayed at
#    scrollTop=0 (oldest messages mounted), the bubble extractor only
#    saw stale bubbles, and the brain reported "no new messages" when
#    James had actually replied hours ago. Verified live via CDP probe.
#
# 2. Content-based: walk up from a ``[data-test="story-container"]``
#    bubble until we find an ancestor with scrollHeight > clientHeight.
#    Layout-agnostic, survives future Upwork class renames as long as
#    the bubble selector keeps existing (which is the only invariant
#    the rest of the extractor depends on too).
#
# 3. Overflow-based (legacy fallback): scan all elements for
#    ``overflow-y: auto/scroll``. Fails on custom-scrollbar wrappers
#    (which use ``overflow: hidden`` + faked scrollbar), which is why
#    we need the content-based pass first.
#
# Returns the scroller element (or null) — caller decides what to do
# with it (scroll up by fraction, jump to bottom, etc.).
_FIND_CHAT_SCROLLER_JS = r"""
    (() => {
        const sels = [
            // 2026 layout — the actual class Upwork ships
            '.scroll-wrapper.custom-scrollbar',
            '.scroll-wrapper',
            // Legacy patterns (older Upwork redesigns)
            '[data-test*="message-list"]',
            '[data-test*="thread"] [class*="scroll"]',
            'main [class*="scroll"]',
            'div[class*="virtual"]',
            'div[class*="MessageList"]',
        ];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el && el.scrollHeight > el.clientHeight + 50) {
                return el;
            }
        }
        // Content-based: walk up from a bubble. Survives DOM renames.
        const bubble = document.querySelector(
            '[data-test="story-container"]'
        );
        if (bubble) {
            let el = bubble.parentElement;
            while (el && el !== document.body) {
                if (el.scrollHeight > el.clientHeight + 50) return el;
                el = el.parentElement;
            }
        }
        // Overflow-based legacy fallback (broken on custom scrollbars).
        let best = null, bestH = 0;
        document.querySelectorAll('*').forEach(el => {
            const st = getComputedStyle(el);
            if ((st.overflowY === 'auto' || st.overflowY === 'scroll') &&
                el.scrollHeight > el.clientHeight + 50 &&
                el.scrollHeight > bestH) {
                best = el; bestH = el.scrollHeight;
            }
        });
        return best;
    })()
"""


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
        r"info|link|page|so|xyz|tech|cloud|live|tv|ly|to|gg|sh)"
        r"(/\S*)?\b",
        re.IGNORECASE,
    ),
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
    #
    # `.desktop-layout-room` is intentionally NOT in this list — it
    # matches Upwork's whole-page chrome wrapper (the `desktop-layout-*`
    # convention denotes layout containers, not per-row elements), so
    # prepending it would let the wait succeed before any actual room
    # mounts. Confirmed 2026-05-20 via live diagnostic (5 inner hrefs:
    # 1 nav + 4 attachments) and reverted to the upstream-original
    # `[data-test="room-item"]` row selector. Prior history: prepended
    # in 12d3593, fork-original (3009e69) used `[data-test="room-item"]`
    # only and worked fine on MiniMax.
    try:
        await page.wait_for_selector(
            '[data-test="rooms-panel"], [data-test="empty-state"], '
            '.rooms-panel-body, [data-test="room-item"]',
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

    # The room-row selector. `.desktop-layout-room` was removed (2026-05-20):
    # it matches Upwork's whole-page chrome wrapper, not individual rows.
    # This is the upstream-original (fork commit 3009e69) chain that worked
    # reliably on MiniMax before the regression introduced in 12d3593.
    _room_selector = '[data-test="room-item"], .room-item, .conversation-item'

    # SPA-route stability gate. `safe_goto` can land on a /rooms/<id> tab
    # that React then re-routes to /ab/messages/rooms/ — the doctype
    # doesn't reload, so an immediate `query_selector_all` runs before
    # the rooms tree finishes mounting and yields 0 rows. Same fix
    # pattern as `get_conversation_messages` lines 609-658: wait for
    # networkidle (best-effort), then poll until the row count is stable
    # for 3 consecutive ticks (~600 ms) before extracting.
    import asyncio as _aio
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        # Upwork's SPA frequently never hits networkidle (long-poll WS,
        # analytics beacons). Non-fatal — the count-stability poll below
        # is the authoritative signal.
        logger.debug("get_messages: networkidle never settled — continuing")
    try:
        prev_count = -1
        stable_ticks = 0
        for _i in range(15):  # max ~3 s budget
            count = len(await page.query_selector_all(_room_selector))
            if count == prev_count and count > 0:
                stable_ticks += 1
                if stable_ticks >= 3:
                    break
            else:
                stable_ticks = 0
            prev_count = count
            await _aio.sleep(0.2)
    except Exception:
        # Non-fatal — fall through to extraction with whatever's mounted.
        logger.debug("get_messages: stability poll failed", exc_info=True)

    # Extract conversation items. Selector matches upstream-original
    # (vanooo/upwork-mcp 3009e69) — no .desktop-layout-room wrapper.
    room_els = await page.query_selector_all(_room_selector)
    if not room_els:
        logger.warning(
            "get_messages: 0 room elements matched at %s "
            "(rooms-panel + [data-test=\"room-item\"] both empty). "
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
            # Drop rows where the extractor couldn't resolve a room id.
            # Without this, the brain receives `{contact_name: "James
            # Blue"}` with no room_id and confabulates "James Blue" as
            # the room_id itself — calling `upwork_get_conversation(
            # room_id="James Blue")` which navigates to the 404 page
            # (2026-05-20 21:21 incident). A clean "no rooms found"
            # is a much better signal than an unusable partial row.
            if not room_id:
                logger.info(
                    "get_messages: dropped row for contact_name=%r "
                    "(no resolvable room_id)",
                    conv.get("contact_name"),
                )
                continue
            # `is_new` is True for any room we haven't recorded before.
            # Drives the "speak to the founder directly" first-contact
            # offer in upwork_inbox_check. False if we've seen the room
            # in any prior sweep, regardless of whether the latest
            # message is new — that's an "ongoing thread" not a fresh
            # client.
            conv["is_new"] = room_id not in seen_rooms
            newly_seen.add(room_id)
            conversations.append(conv)
        except Exception:
            logger.exception("get_messages: per-row extraction raised")
            continue

    if newly_seen:
        _save_seen_rooms(seen_rooms | newly_seen)

    # ── URL-fallback for open-conversation view ───────────────────────
    # Live incident 2026-05-21 12:58:33: the host Brave tab was sitting
    # on James Blue's open conversation view. `safe_goto` navigated to
    # /ab/messages/rooms/ but Upwork's SPA re-opens the last conversation
    # — so the rooms-list sidebar never rendered, and both
    # `[data-test="rooms-panel"]` and `[data-test="room-item"]` returned
    # 0 (matching the "0 room elements matched" WARNING above). But the
    # real `room_<hex>` was sitting right there in `page.url`.
    #
    # Recover by mining the URL when no rows mounted. This is strictly
    # additive — the original "0 rows" WARNING still fires for the
    # genuine empty-inbox / layout-drift case (URL won't match the regex
    # because the inbox URL is `/ab/messages/rooms/` with no id segment).
    if not conversations:
        cur_url = getattr(page, "url", "") or ""
        # Same id shapes that _validate_room_id accepts as bare ids:
        # room_<hex>, strict UUID (8-4-4-4-12 lowercase hex), or numeric.
        m = re.search(
            r"/ab/messages/rooms/(room_[A-Za-z0-9_-]+|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"
            r"\d+)(?:[/?#]|$)",
            cur_url,
        )
        if m:
            room_id = m.group(1)
            # `page.title()` typically returns the contact's name when
            # the conversation view is open. Reject Upwork's generic
            # inbox-default titles — they aren't contact names.
            try:
                title = (await page.title()) or ""
            except Exception:
                title = ""
            title = title.strip()
            contact_name = (
                title
                if title
                and title.lower() not in {"messages", "upwork", "messages | upwork"}
                else ""
            )
            synthetic = {
                "room_id": room_id,
                "room_url": cur_url,
                "contact_name": contact_name,
                # Tag so the brain (and post-mortems) can tell this
                # row didn't come from the rooms-list sidebar.
                "source": "page_url_fallback",
            }
            logger.info(
                "get_messages: returning URL-derived synthetic conv "
                "room_id=%s contact_name=%r (sidebar empty, "
                "conversation view open)",
                room_id, contact_name,
            )
            conversations = [synthetic]

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
    # Accepted shapes (in order of preference):
    #   1. /ab/messages/rooms/room_<hex>?companyReference=...&sidebar=true
    #   2. /ab/messages/rooms/<uuid>          (2026 migration — strict)
    #   3. /nx/messages/<numeric-id>          (legacy)
    #
    # Upwork row DOM exposes MULTIPLE ``a[href*="/messages/"]`` anchors
    # per row — the real room link plus auxiliary anchors pointing at
    # attachments (``/ab/messages/att/<uuid>``), file previews
    # (``/ab/messages/file/...``), and nav/skeleton placeholders (the
    # bare inbox URL ``/ab/messages/rooms``).
    #
    # Earlier parsers either:
    #   • grabbed the FIRST anchor (polluting room_url with the inbox
    #     URL), or
    #   • accepted "any non-rooms first segment" — the latter is what
    #     harvested ``att`` and bare attachment UUIDs as room_ids.
    #   • OR were too strict, rejecting ALL UUIDs — which made the
    #     2026-05-20 19:23 incident: Upwork has migrated some/all real
    #     rooms to UUID-shape paths (``/ab/messages/rooms/<uuid>``),
    #     and James Blue's row in the current DOM has NO ``room_<hex>``
    #     anchor at all. That row became unreachable because the
    #     extractor refused all UUIDs.
    #
    # Fix — three-pass strict scan with re-introduced UUID branch:
    #   Pass 1: ``room_<hex>`` always wins. Lock the FIRST match and
    #           break.
    #   Pass 2 (only if Pass 1 found nothing): accept strict UUID at
    #           ``/ab/messages/rooms/<uuid>`` ONLY (path-equality,
    #           lowercase hex, no extra path segments before or after
    #           the UUID). This is what distinguishes a real room
    #           anchor from an attachment-UUID anchor at
    #           ``/ab/messages/att/<uuid>`` or
    #           ``/ab/messages/rooms/<uuid>/something``.
    #   Pass 3 (only if Pass 1 + 2 found nothing): accept legacy
    #           /nx/messages/<numeric-id> only (Upwork legacy ids are
    #           numeric — slugs are not real rooms).
    # Anything else — /att/<uuid>, /file/<id>, uppercase UUID, UUID with
    # trailing segment, alphanumeric /nx/messages/<slug>, bare inbox URL
    # — is rejected. Partial conv is safer than seeding downstream
    # with a non-room URL.
    candidates = await el.query_selector_all('a[href*="/messages/"]')
    chosen_href: str | None = None
    chosen_room_id: str | None = None

    # Legacy numeric /nx/messages/<digits>. Reject slug/word forms.
    _nx_legacy_pat = re.compile(
        r"(?:^|^https?://[^/]+)/nx/messages/(\d+)(?:[/?#]|$)",
        re.IGNORECASE,
    )

    # Strict UUID at /ab/messages/rooms/<uuid> only. Path-equality:
    #   • absolute (https?://host) or relative
    #   • path is EXACTLY /ab/messages/rooms/<uuid> with optional
    #     trailing slash
    #   • UUID is strict 8-4-4-4-12 lowercase hex
    #   • nothing after the UUID except optional trailing slash
    # Query/fragment are stripped before matching so the regex doesn't
    # need to model them. Rules out:
    #   /ab/messages/att/<uuid>       (intermediate segment)
    #   /ab/messages/file/<uuid>      (intermediate segment)
    #   /ab/messages/rooms/<uuid>/x   (trailing segment)
    #   /ab/messages/rooms/<UPPER>    (uppercase hex)
    _uuid_room_pat = re.compile(
        r"^(?:https?://[^/]+)?/ab/messages/rooms/"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$"
    )

    # Pass 1: room_<hex> always wins. Lock the FIRST match and break.
    for cand in candidates:
        href = await cand.get_attribute("href")
        if not href:
            continue
        m = re.search(r"room_[A-Za-z0-9_-]+", href)
        if m:
            chosen_href = href
            chosen_room_id = m.group(0)
            break

    # Pass 2: only if no room_<hex> was found, accept strict UUID at
    # /ab/messages/rooms/<uuid>. First match wins.
    if chosen_href is None:
        for cand in candidates:
            href = await cand.get_attribute("href")
            if not href:
                continue
            # Strip query + fragment before path-equality match.
            path_only = href.split("#", 1)[0].split("?", 1)[0]
            um = _uuid_room_pat.match(path_only)
            if um:
                chosen_href = href
                chosen_room_id = um.group(1)
                break

    # Pass 3: only if no room_<hex> AND no strict-UUID, accept legacy
    # numeric /nx/messages/<id>. First valid match wins.
    if chosen_href is None:
        for cand in candidates:
            href = await cand.get_attribute("href")
            if not href:
                continue
            nm = _nx_legacy_pat.search(href)
            if nm:
                chosen_href = href
                chosen_room_id = nm.group(1)
                break

    if chosen_href:
        conv["room_url"] = (
            chosen_href
            if chosen_href.startswith("http")
            else f"https://www.upwork.com{chosen_href}"
        )
        if chosen_room_id:
            conv["room_id"] = chosen_room_id
    elif candidates:
        # Row had message-link anchors but none resolved to a per-room
        # URL. Most likely a virtualized skeleton row, attachment-only
        # anchor, or unknown shape — surface it so the upstream
        # rooms-panel-stability poll can be tuned.
        #
        # Diagnostic: dump the actual hrefs we scanned (first 5, each
        # truncated to 200 chars). Upwork has shipped at least 3 layout
        # variants since the 2026 migration — without the raw hrefs we
        # can't tell whether the row genuinely has no room link or
        # whether the room URL is in a shape we don't recognize yet.
        scanned_hrefs: list[str] = []
        for cand in candidates[:5]:
            try:
                href = await cand.get_attribute("href")
            except Exception:
                href = None
            if href:
                scanned_hrefs.append(href[:200])
            else:
                scanned_hrefs.append("<no-href>")
        logger.warning(
            "room-list row had %d message anchors but NONE resolved to a "
            "known room-id shape; skipping room_url/room_id for "
            "contact_name=%r. Scanned hrefs (first 200 chars each): %r",
            len(candidates),
            conv.get("contact_name"),
            scanned_hrefs,
        )

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
    # P0a — reject the bare inbox URL BEFORE we touch the browser.
    # Without this, ``startswith("http")`` would navigate verbatim and
    # the bubble extractor would scrape whatever happened to be open
    # in the shared Brave profile (2026-05-20 incident).
    _validate_room_id(room_id)

    browser = get_browser()
    await browser.ensure_logged_in()

    # Build URL — Upwork moved /nx/messages/ → /ab/messages/rooms/ in the 2026 redesign.
    if room_id.startswith("http"):
        url = room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{room_id}"

    # safe_goto now performs a post-nav URL check and raises
    # UpworkRoomNotFound if Upwork's SPA silently redirected the room
    # URL to /ab/messages. Translate that into a structured error so
    # the brain gets a groundable signal instead of a 500 / silent
    # inbox scrape (2026-05-20 16:48 incident).
    #
    # force_reload=True is REQUIRED on the read path. The picked Brave
    # tab is often already sitting on the target room URL (the user
    # opens conversations manually, or a prior MCP call left the tab
    # there). A bare goto to the same URL is a no-op for the SPA — the
    # React tree doesn't remount, the Service Worker at /ab/messages/sw.js
    # serves the page from cache, and the scroll-to-bottom + render-
    # stability poll succeeds INSTANTLY on a frozen DOM. The bubble
    # extractor then returns the same stale messages forever. Root
    # cause 2026-05-24: identical "10:37 PM" timestamps returned on
    # the James Blue thread across hour-apart calls even though those
    # bubbles were ≥5 days old. ``page.reload`` is distinct enough at
    # the browser layer to defeat both the SPA short-circuit and the
    # SW cache.
    try:
        page = await browser.safe_goto(url, force_reload=True)
    except UpworkRoomNotFound as exc:
        logger.warning(
            "get_conversation_messages: room not found — requested=%s redirected_to=%s",
            exc.requested_url, exc.final_url,
        )
        return {
            "error": "room_not_found",
            "requested": exc.requested_url,
            "redirected_to": exc.final_url,
            "message": (
                "The Upwork room URL silently redirected away from the "
                "requested conversation — the room id is invalid, "
                "deleted, or not accessible to this account. Call "
                "`upwork_get_messages` to list current rooms."
            ),
        }

    # Give Tiptap + the thread renderer a moment to mount after nav.
    import asyncio as _aio
    await _aio.sleep(2)

    # Force the virtualized message list to render the NEWEST bubbles.
    # Upwork's chat is a virtualized list — it mounts ~10-30 bubbles
    # around the current scroll position, leaving everything else
    # un-rendered. Two failure modes hit us before the explicit-scroll
    # path was added:
    #   1. Upwork restores the previous scroll position on navigation,
    #      so if the tab was last scrolled to the TOP (e.g. by a prior
    #      "scroll to load history" pass), `safe_goto` lands you at the
    #      top and the newest bubble at the bottom is NOT mounted.
    #   2. Right after a container/host-bridge restart the renderer
    #      lags — the 2s sleep above is enough for Tiptap but not for
    #      the message list to settle.
    # Verified live 2026-05-17 21:29:58: brain called this 1 min after
    # container restart; MCP returned a snapshot ending at the 10:09 PM
    # offer; James's 9:12 PM reply (sent ~12 min earlier) was not in
    # the DOM at all. Manual MCP call 4 min later (warm browser) DID
    # return it — same code path, just a settled DOM.
    #
    # Fix: scroll the chat container to bottom and wait for the
    # rendered bubble count to stabilize before extraction. We poll
    # because Upwork doesn't expose a "list-rendered" event — count
    # stability across 3 consecutive ticks (~600 ms) is the cheapest
    # signal that virtualization has finished.
    try:
        prev_count = -1
        stable_ticks = 0
        for _i in range(15):  # max ~3 s budget
            tick = await page.evaluate(
                r"""
                (() => {
                    const scroller = """ + _FIND_CHAT_SCROLLER_JS.strip() + r""";
                    if (scroller) {
                        scroller.scrollTop = scroller.scrollHeight;
                    }
                    const count = document.querySelectorAll(
                        '[data-test="story-container"]'
                    ).length;
                    return {scrolled: !!scroller, count};
                })()
                """
            )
            count = (tick or {}).get("count", 0)
            if count == prev_count and count > 0:
                stable_ticks += 1
                if stable_ticks >= 3:
                    break
            else:
                stable_ticks = 0
            prev_count = count
            await _aio.sleep(0.2)
    except Exception:
        # Non-fatal — fall through to the legacy extraction. Worst case
        # we extract whatever's mounted, same as the pre-fix behavior.
        logger.debug("scroll-to-bottom warmup failed", exc_info=True)

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

    # Fix 1: log bubble count after warmup so silent extraction failures
    # are diagnosable. The earlier "0 messages" returns gave the brain no
    # signal — same pattern as the rooms-list "0 row elements matched"
    # warning in get_messages (this file, around line 303).
    logger.info(
        "get_conversation_messages: %d bubble container(s) matched at %s",
        len(containers), getattr(page, "url", "?"),
    )

    # Fix 2: widen bubble selector with secondary scan. Upwork has shipped
    # several layout variants since the 2026 migration — when the primary
    # `[data-test="story-container"]` (and legacy fallbacks) return 0,
    # try modern ARIA / qa-test / substring variants before giving up.
    # Same shape as the inbox-side selector-chain widening landed
    # 2026-05-20.
    if not containers:
        containers = await page.query_selector_all(
            '[role="listitem"], [data-qa*="message"], div[class*="story-"]'
        )
        logger.info(
            "get_conversation_messages: primary selector empty, widened "
            "fallback matched %d at %s",
            len(containers), getattr(page, "url", "?"),
        )

    # Fix 3: a11y-tree fallback when bubble selectors all return empty AND
    # we're on a real conversation URL. Playwright exposes
    # `page.accessibility.snapshot()` (an ARIA tree of the current page);
    # Upwork's chat panes are ARIA-rich, so message text is reachable
    # through the accessibility tree even when the DOM hooks
    # (`story-container` etc.) drift. Cheapest fallback before giving up
    # — no extra LLM call, no tab contention. Reserved for "all selectors
    # failed" path: the regular extraction loop below is preferred when
    # any container matched.
    if not containers:
        cur_url = getattr(page, "url", "") or ""
        is_on_room = bool(re.search(
            r"/ab/messages/rooms/(?:room_[A-Za-z0-9_-]+|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)",
            cur_url,
        ))
        if is_on_room:
            try:
                a11y = await page.accessibility.snapshot()
            except Exception as exc:
                logger.warning(
                    "get_conversation_messages: a11y snapshot failed: %s",
                    exc,
                )
                a11y = None
            if a11y:
                # Flatten the ARIA tree into a single readable string.
                # Cap total at 30 KB and depth at 8 so a pathological
                # tree can't blow up the return. Skip nodes with no
                # name and no interactive role.
                def _flatten(node, depth=0, out=None):
                    out = out if out is not None else []
                    if depth > 8 or sum(len(s) for s in out) > 30_000:
                        return out
                    name = (node.get("name") or "").strip()
                    role = node.get("role") or ""
                    if name or role in {"button", "link", "img"}:
                        out.append(f"{'  ' * depth}[{role}] {name}")
                    for child in (node.get("children") or []):
                        _flatten(child, depth + 1, out)
                    return out
                tree_text = "\n".join(_flatten(a11y))
                logger.info(
                    "get_conversation_messages: bubbles empty — a11y "
                    "fallback emitted %d chars",
                    len(tree_text),
                )
                return {
                    "messages": [],
                    "contact_name": "",
                    "room_id": room_id,
                    "room_url": cur_url,
                    "dom_fallback": tree_text[:30_000],
                    "source": "a11y_fallback",
                }

    # Track sender across consecutive bubbles — Upwork sometimes only
    # renders the header on the FIRST bubble of a run from the same
    # person, leaving subsequent bubbles with body only. Carry forward
    # the last-known sender so each emitted message has author info.
    # ALSO track last_is_mine so the extractor can detect a speaker
    # flip and refuse to inherit the wrong sender across it (see
    # _extract_message docstring for the 2026-05-17 incident that
    # motivated this).
    last_sender: str | None = None
    last_timestamp: str | None = None
    last_is_mine: bool | None = None
    _ctx_contact = conversation.get("contact_name")
    if isinstance(_ctx_contact, str) and _is_nav_noise(_ctx_contact):
        _ctx_contact = None

    # Iterative scroll-up accumulation (2026-05-24). The single-pass
    # path used to take only the trailing ``[-limit:]`` slice of the
    # mounted bubbles — which broke whenever Brave's tab was scrolled
    # away from the bottom (today's incident: 20 mounted but they were
    # the OLDEST 20, so the newest 13 messages were silently missed).
    # ``_gather_all_bubbles_iter`` scrolls UP from the current bottom-
    # warmed position, dedups by content fingerprint, and returns the
    # bubbles in chronological order — top of chat = oldest first.
    try:
        ordered_handles, _gather_dropped = await _gather_all_bubbles_iter(
            page, target_count=limit,
        )
    except Exception:
        # Defensive fallback: if the iterative gatherer raised, fall back
        # to the original single-pass behavior with the already-mounted
        # containers. Same data path as before the fix, just less complete.
        logger.warning(
            "get_conversation_messages: iterative gatherer raised, "
            "falling back to single-pass extraction",
            exc_info=True,
        )
        ordered_handles = list(containers)
        _gather_dropped = 0

    logger.info(
        "get_conversation_messages: iterative gather settled — "
        "%d unique bubbles (target=%d, fingerprint_failures=%d)",
        len(ordered_handles), limit, _gather_dropped,
    )

    for el in ordered_handles[-limit:]:
        try:
            msg = await _extract_message(
                el,
                last_sender=last_sender,
                last_timestamp=last_timestamp,
                last_is_mine=last_is_mine,
                me_name=me_name,
                contact_name=_ctx_contact,
            )
            if msg:
                last_sender = msg.get("sender") or last_sender
                last_timestamp = msg.get("timestamp") or last_timestamp
                last_is_mine = msg.get("is_mine")
                # is_mine override: if caller passed me_name (e.g.
                # "Vato Tchipa" from lazyclaw's display_name), match
                # the sender against it. This is the only reliable
                # signal — Upwork's bubble class hints (.outgoing
                # / [class*="self"]) are stale on the 2026 layout,
                # but combined with the flip-aware carry-forward
                # above they now stay self-consistent.
                if me_name and msg.get("sender"):
                    msg["is_mine"] = _sender_matches(msg["sender"], me_name)
                    last_is_mine = msg["is_mine"]
                # P0b — drop bubbles whose timestamps are in the future
                # (relative to local clock + small skew buffer). A
                # future timestamp means the extractor latched onto
                # unrelated DOM (a "Last seen 2:11 PM" tag, a status
                # badge, a notification banner). Real chat bubbles
                # never carry a future clock. We log the drop with the
                # class hint + raw timestamp so post-mortem diagnosis
                # can tell extraction-error from a real Upwork bug.
                raw_ts = msg.get("timestamp")
                if isinstance(raw_ts, str) and _bubble_timestamp_in_future(raw_ts):
                    try:
                        class_hint = await el.get_attribute("class") or ""
                    except Exception:
                        class_hint = "<unavailable>"
                    logger.warning(
                        "get_conversation_messages: dropping bubble with "
                        "future timestamp raw=%r class=%r sender=%r",
                        raw_ts, class_hint, msg.get("sender"),
                    )
                    continue

                # Orphan-bubble continuation fix (2026-05-23). See
                # _resolve_orphan_bubble below.
                if not msg.get("sender"):
                    _resolve_orphan_bubble(msg, conversation["messages"])
                    continue

                conversation["messages"].append(msg)
        except Exception:
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


# ── room_id guard (P0a) ──────────────────────────────────────────────
# Live incident 2026-05-20 13:34–13:35: the brain called
# ``upwork_get_conversation(room_id="https://www.upwork.com/ab/messages/rooms",
# ...)`` — i.e. the bare inbox URL with no specific room ID appended.
# Because ``get_conversation_messages`` accepts URL-shaped ``room_id``s
# (``startswith("http")`` → navigate verbatim), the browser landed on
# the inbox page and the bubble extractor scraped whatever conversation
# happened to be open in the shared Brave profile, producing 4 KB of
# garbage with a (fake-looking) "2:11 PM" timestamp. The brain then
# quoted the garbage as if it were the requested thread. Closes the
# fetch-layer half of the most recent stale-Upwork-DM confabulation —
# the grounding-layer half (quote-then-summarize, most-recent-wins)
# was hardened in commit c83e39c.
#
# Reject:
#   * empty / whitespace-only
#   * the bare inbox URL with or without trailing ``/`` / ``?`` / ``#``
#   * any URL under ``/ab/messages/rooms`` that lacks a ``/<id>``
#     segment after ``rooms`` (e.g. ``/ab/messages/rooms?filter=unread``)
#
# Accept:
#   * legacy bare IDs (e.g. ``room_12345abc``)
#   * full conversation URLs (``.../rooms/room_12345abc?companyReference=...``)

_INBOX_PATH = "/ab/messages/rooms"

# Shapes accepted as bare (non-URL) room ids:
#   * ``room_<alphanum_underscore_dash>`` — legacy + 2026 hex prefix shape
#   * Strict UUID 8-4-4-4-12 lowercase hex — 2026 migration shape
#   * Pure numeric — legacy /nx/messages/<digits>
#
# Anything else (free-form text like a contact name, partial id with a
# space, slash-prefixed paths handled separately below) is REFUSED with
# a contact-name-shaped error. This closes the 2026-05-20 21:21 incident
# where the brain called ``upwork_get_conversation(room_id="James Blue")``
# and the bare-string slipped past the inbox-URL guard, navigated to
# ``/ab/messages/rooms/James Blue``, got redirected to the 404 page,
# and the bubble extractor scraped the "Looking for something?" surface.
_BARE_ROOM_ID_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^room_[A-Za-z0-9_-]+$"),
    re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    re.compile(r"^\d+$"),
)


def _looks_like_known_bare_room_id(value: str) -> bool:
    """True iff ``value`` matches one of the known bare room-id shapes.

    Strictly anchored — partial matches don't count. Used by
    ``_validate_room_id`` to reject contact-name-shaped strings before
    they reach the browser.
    """
    return any(p.match(value) for p in _BARE_ROOM_ID_SHAPES)


def _validate_room_id(room_id: str | None) -> None:
    """Validate ``room_id`` before any browser navigation.

    Raises :class:`ValueError` with an actionable message if the input
    is missing, empty, or points at the inbox instead of a specific
    conversation. The error string explicitly tells the caller (brain
    or NL skill) how to recover: fetch the inbox via
    :func:`get_messages` first, then pass the returned ``room_id``.
    """
    if room_id is None:
        raise ValueError(
            "room_id is required — call `upwork_get_messages` first, "
            "pass the returned `room_id` to `upwork_get_conversation` "
            "— never the inbox URL"
        )
    if not isinstance(room_id, str) or not room_id.strip():
        raise ValueError(
            "room_id must be a non-empty string — call "
            "`upwork_get_messages` first, pass the returned `room_id` "
            "to `upwork_get_conversation` — never the inbox URL"
        )

    cleaned = room_id.strip()

    # Strip trailing fragment / query so e.g. ``.../rooms?filter=unread``
    # and ``.../rooms#top`` collapse to the inbox shape before the
    # equality check.
    no_fragment = cleaned.split("#", 1)[0]
    no_query = no_fragment.split("?", 1)[0]
    no_trailing_slash = no_query.rstrip("/")

    inbox_url_https = "https://www.upwork.com" + _INBOX_PATH
    inbox_url_http = "http://www.upwork.com" + _INBOX_PATH
    if no_trailing_slash in (inbox_url_https, inbox_url_http):
        raise ValueError(
            f"room_id={room_id!r} is the inbox URL, not a specific "
            "conversation — call `upwork_get_messages` first, pass the "
            "returned `room_id` to `upwork_get_conversation` — never "
            "the inbox URL"
        )

    # Any URL touching /ab/messages/rooms MUST have a /<id> segment
    # after the literal "rooms". Catches relative paths and stripped
    # variants (e.g. user passes "/ab/messages/rooms/").
    if _INBOX_PATH in cleaned:
        tail = cleaned.split(_INBOX_PATH, 1)[1]
        # Tail must start with "/" followed by a non-empty, non-query,
        # non-fragment segment — i.e. an actual room id.
        tail_no_query = tail.split("?", 1)[0].split("#", 1)[0]
        segments = [s for s in tail_no_query.split("/") if s]
        if not segments:
            raise ValueError(
                f"room_id={room_id!r} points at the inbox path with no "
                "specific room — call `upwork_get_messages` first, "
                "pass the returned `room_id` to `upwork_get_conversation` "
                "— never the inbox URL"
            )

    # URL-shaped inputs (absolute http(s):// or path-relative starting
    # with /) pass the URL-segment check above and are accepted from
    # here. Anything else is a BARE id — it must match one of the
    # known shapes (room_<...>, strict UUID, or pure numeric). A
    # bare string with whitespace, slashes, or free-form letters is
    # almost certainly a contact name (e.g. "James Blue", "James")
    # that the brain confused for a room id — reject loudly with the
    # same actionable recovery message as the inbox-URL guard.
    is_url_shaped = (
        cleaned.startswith("http://")
        or cleaned.startswith("https://")
        or cleaned.startswith("/")
    )
    if not is_url_shaped and not _looks_like_known_bare_room_id(cleaned):
        raise ValueError(
            f"room_id={room_id!r} looks like a contact name, not a "
            "room id. Call `upwork_get_messages` first and pass the "
            "returned `room_id` to `upwork_get_conversation` — never "
            "a contact name."
        )


# Parses 12-hour clock strings like "10:39 PM" / "2:11 PM" / "9:20 PM PDT"
# back into (hour, minute) tuples so we can compare bubble timestamps
# against ``now()`` and drop bubbles whose clocks lie in the future. We
# DON'T introduce a dateparser dependency for this — Upwork only emits
# the time-of-day, and a 30-line regex parser covers every shape the
# extractor has produced over the 2026 layout. Returns None on any
# unparseable input — callers treat None as "keep" so a parser failure
# never silently drops a real message.
_TIME_OF_DAY_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?\s*(?:[A-Z]{2,4})?\s*$"
)


def _parse_time_of_day(raw: str | None) -> tuple[int, int] | None:
    """Best-effort parse of a bubble timestamp string into 24h
    ``(hour, minute)``. Returns None when the input doesn't look like
    Upwork's time-of-day format — caller treats None as "don't drop".
    """
    if not raw or not isinstance(raw, str):
        return None
    m = _TIME_OF_DAY_RE.match(raw)
    if not m:
        return None
    try:
        hour = int(m.group(1))
        minute = int(m.group(2))
    except (TypeError, ValueError):
        return None
    if not (0 <= minute <= 59):
        return None
    suffix = (m.group(3) or "").upper()
    if suffix == "AM":
        if not (1 <= hour <= 12):
            return None
        if hour == 12:
            hour = 0
    elif suffix == "PM":
        if not (1 <= hour <= 12):
            return None
        if hour != 12:
            hour += 12
    else:
        # 24-hour clock fallback (Upwork's regional layouts sometimes
        # render without AM/PM)
        if not (0 <= hour <= 23):
            return None
    return hour, minute


# Date hints in bubble timestamps. Upwork's chat usually shows just
# "10:37 PM" with no date — those are ambiguous (could be today, could be
# yesterday). Only DROP as "future" when the bubble explicitly carries a
# date marker that resolves to today/tomorrow/a later calendar day.
# The 2026-05-21 22:13 incident: 4 real bubbles ("10:37 PM" James Blue,
# "10:30 PM" Vato) got dropped at 22:14 because the old filter assumed
# "same day" and saw 22:37 > 22:14 → future → drop. They were yesterday's
# messages. With date-aware filtering, time-only strings now KEEP.
_TODAY_PREFIX_RE = re.compile(r"\bToday\b", re.IGNORECASE)
_TOMORROW_PREFIX_RE = re.compile(r"\bTomorrow\b", re.IGNORECASE)
_YESTERDAY_PREFIX_RE = re.compile(r"\bYesterday\b", re.IGNORECASE)
_MONTH_NAME_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\b",
    re.IGNORECASE,
)
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _timestamp_has_date(raw: str | None) -> bool:
    """True if ``raw`` carries an explicit date marker (not just HH:MM).

    Catches: "Today 10:37 PM", "Yesterday 9:20 PM", "Tomorrow 8:00 AM",
    "May 21 10:37 PM", "5/21 10:37 PM", "2026-05-21 22:37".
    Misses (intentionally): plain "10:37 PM" / "9:20 PM PDT" / "13:30" —
    those are ambiguous and treated as "no date hint" → KEEP.
    """
    if not raw or not isinstance(raw, str):
        return False
    if _TODAY_PREFIX_RE.search(raw):
        return True
    if _TOMORROW_PREFIX_RE.search(raw):
        return True
    if _YESTERDAY_PREFIX_RE.search(raw):
        return True
    if _MONTH_NAME_RE.search(raw):
        return True
    if _SLASH_DATE_RE.search(raw):
        return True
    if _ISO_DATE_RE.search(raw):
        return True
    return False


def _bubble_timestamp_in_future(
    raw_timestamp: str | None, *, now=None, skew_seconds: int = 60
) -> bool:
    """Return True iff ``raw_timestamp`` resolves to a moment more than
    ``skew_seconds`` after ``now``. Unparseable inputs return False
    (= "keep"). A bubble with a future timestamp signals the extractor
    latched onto unrelated DOM (a "Last seen 2:11 PM" tag, a notification
    badge, etc.).

    Date-aware (2026-05-21 fix): plain time-of-day strings like "10:37 PM"
    with no date prefix are KEPT regardless of clock comparison — they
    could be yesterday's messages, not future ones. Only drop when the
    bubble has an explicit date hint AND that hint resolves to the future:
      - "Tomorrow ..."         → DROP
      - "Today HH:MM" + HH:MM > now  → DROP
      - "Yesterday ..."        → KEEP (definitionally past)
      - "May 22 ..." (today is May 21) → DROP
      - "10:37 PM" (no date)   → KEEP (assume past/yesterday)
    """
    import datetime as _dt

    if not raw_timestamp or not isinstance(raw_timestamp, str):
        return False

    if now is None:
        now = _dt.datetime.now()

    # Explicit future-date prefixes — always drop.
    if _TOMORROW_PREFIX_RE.search(raw_timestamp):
        return True
    # Explicit past-date prefixes — always keep.
    if _YESTERDAY_PREFIX_RE.search(raw_timestamp):
        return False

    # "Today HH:MM" — compare against now using HH:MM-within-day.
    if _TODAY_PREFIX_RE.search(raw_timestamp):
        # Strip "Today" and re-parse the remainder as time-of-day.
        remainder = _TODAY_PREFIX_RE.sub("", raw_timestamp, count=1).strip()
        parsed = _parse_time_of_day(remainder)
        if parsed is None:
            return False
        hour, minute = parsed
        bubble_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return (bubble_dt - now).total_seconds() > skew_seconds

    # Calendar-date markers (Month name, M/D, ISO). If present AND parseable
    # into a date that strictly follows today → drop. Otherwise keep.
    # We don't try to fully parse here (no dateparser dep): if a date hint
    # is present, fall through to the time-only check below for the HH:MM
    # part. If the date hint matches today's month/day, treat as today.
    today = now.date()
    has_calendar_hint = bool(
        _ISO_DATE_RE.search(raw_timestamp)
        or _SLASH_DATE_RE.search(raw_timestamp)
        or _MONTH_NAME_RE.search(raw_timestamp)
    )

    if has_calendar_hint:
        # Try ISO first (most precise).
        iso_m = _ISO_DATE_RE.search(raw_timestamp)
        if iso_m:
            try:
                parsed_date = _dt.date.fromisoformat(iso_m.group(0))
                if parsed_date > today:
                    return True  # future calendar date → drop
                if parsed_date < today:
                    return False  # past calendar date → keep
                # Same day → fall through to HH:MM compare below.
            except ValueError:
                pass

        # Slash form like "5/22" or "5/22/2026". If we can parse it and
        # it's strictly after today, drop.
        slash_m = _SLASH_DATE_RE.search(raw_timestamp)
        if slash_m:
            parts = slash_m.group(0).split("/")
            try:
                month = int(parts[0])
                day = int(parts[1])
                year = int(parts[2]) if len(parts) > 2 else today.year
                if year < 100:
                    year += 2000
                parsed_date = _dt.date(year, month, day)
                if parsed_date > today:
                    return True
                if parsed_date < today:
                    return False
            except (ValueError, IndexError):
                pass

        # Month-name form. Don't try to fully parse — if the month doesn't
        # match today's, it's ambiguous (could be last year, could be next
        # year). Conservatively KEEP to avoid dropping real messages.
        # Future calendar-date drops are best-effort, not authoritative.

    # No date hint → ambiguous, KEEP (the safer behavior post-fix).
    return False


async def _gather_all_bubbles_iter(
    page,
    *,
    target_count: int,
    max_iterations: int = 25,
    no_progress_limit: int = 2,
    scroll_step_fraction: float = 0.7,
    settle_seconds: float = 0.4,
) -> tuple[list, int]:
    """Iteratively scroll the chat container UP from the current position,
    accumulating all unique bubble element handles.

    Why this exists (2026-05-24 fix): Upwork's chat list is virtualized.
    Only ~20 bubbles around the current scroll position are mounted in the
    DOM at any time. A single-pass extraction returns whatever happened to
    be mounted — usually the newest 20, but if Brave's tab was previously
    scrolled to the top (e.g. user reviewed history), the OLDEST 20 are
    mounted instead. Today's incident: brain's tool call saw 20 mounted
    bubbles but returned only the 5 oldest because Brave was scrolled up
    and the newer 13 were never mounted.

    Fix: scroll UP iteratively from the current position (which the
    caller has already scrolled to bottom). On each iteration, capture
    every mounted bubble's absolute Y position + a content fingerprint
    in ONE JS call (atomic snapshot). Dedup across iterations by
    fingerprint. Stop when:
      - We've accumulated ``target_count + 5`` unique bubbles, OR
      - Two consecutive iterations add zero new bubbles (top reached
        or scroll wedged), OR
      - ``max_iterations`` exceeded (safety brake).

    Returns ``(element_handles_chronological, dropped_count)``:
      - ``element_handles_chronological``: list of Playwright element
        handles, sorted by absolute Y (top of chat = oldest = first).
        Caller passes each handle through ``_extract_message`` as
        before.
      - ``dropped_count``: how many bubbles the gatherer SAW but
        couldn't fingerprint (rare — only when JS eval failed). Useful
        for the diagnostic log so silent extraction failures surface.

    Pure WRT page state: scrolls but doesn't click / type / navigate.
    Defensive: every JS call is wrapped; failure falls through with
    whatever was gathered so far.
    """
    import asyncio as _aio

    seen_keys: dict[str, tuple[float, object]] = {}
    no_progress = 0
    dropped = 0

    for iter_n in range(max_iterations):
        # Pick selector chain to query bubble containers. Same chain as
        # the caller's single-pass extraction, kept in sync so that if
        # one path matches the other does too.
        containers = await page.query_selector_all(
            '[data-test="story-container"]'
        )
        if not containers:
            containers = await page.query_selector_all(
                '[data-test="message"], .message-item, .chat-message'
            )
        if not containers:
            containers = await page.query_selector_all(
                '[role="listitem"], [data-qa*="message"], div[class*="story-"]'
            )

        if not containers:
            # Nothing mounted — wait one beat then break. The caller's
            # outer fallback chain handles "0 containers" via a11y tree.
            break

        new_count_this_iter = 0
        for el in containers:
            # One JS eval per bubble to get absolute-Y + a stable
            # fingerprint. This is the slow path (~20 ms per bubble);
            # keeping it inline because batching via evaluate_all would
            # need element-handle round-trip plumbing that's not worth
            # the complexity for ≤50 bubbles per iteration.
            try:
                probe = await el.evaluate(r"""
                    (el) => {
                        const rect = el.getBoundingClientRect();
                        let scroller = el.closest('[class*="scroll"]');
                        if (!scroller) scroller = document.scrollingElement || document.documentElement;
                        const absY = rect.top + (scroller ? scroller.scrollTop : 0);
                        const h = el.querySelector('[data-test="story-header"]');
                        const b = el.querySelector('[data-test="story-message"]');
                        const headerTxt = (h && h.textContent || '').trim();
                        const bodyTxt = (b && b.textContent || '').trim().slice(0, 200);
                        return [absY, headerTxt + '|' + bodyTxt];
                    }
                """)
            except Exception:
                dropped += 1
                continue

            if not probe or not isinstance(probe, (list, tuple)) or len(probe) != 2:
                dropped += 1
                continue
            absY, fp = probe[0], probe[1]
            # Skeleton check: a bubble with empty header AND empty body
            # fingerprints to literal "|" — virtualization placeholder,
            # no real content. Skip silently (not a fingerprint failure).
            if not fp or fp.strip("|").strip() == "":
                continue

            if fp in seen_keys:
                continue

            seen_keys[fp] = (float(absY) if absY is not None else 0.0, el)
            new_count_this_iter += 1

        if len(seen_keys) >= target_count + 5:
            logger.info(
                "get_conversation_messages: scroll-iter %d: hit target "
                "(%d unique bubbles, target=%d)",
                iter_n + 1, len(seen_keys), target_count,
            )
            break

        if new_count_this_iter == 0:
            no_progress += 1
            if no_progress >= no_progress_limit:
                logger.info(
                    "get_conversation_messages: scroll-iter %d: stopped "
                    "(no new bubbles for %d iterations; %d unique total)",
                    iter_n + 1, no_progress_limit, len(seen_keys),
                )
                break
        else:
            no_progress = 0

        # Scroll UP by ``scroll_step_fraction`` of the visible viewport.
        # Returns False when we're already at the top (scrollTop == 0)
        # OR when the scroller selector chain finds nothing scrollable.
        try:
            scrolled_up = await page.evaluate(
                r"""
                (frac) => {
                    const scroller = """ + _FIND_CHAT_SCROLLER_JS.strip() + r""";
                    if (!scroller) return false;
                    const before = scroller.scrollTop;
                    if (before <= 0) return false;
                    scroller.scrollTop = Math.max(0, before - (scroller.clientHeight * frac));
                    return scroller.scrollTop < before;
                }
                """,
                scroll_step_fraction,
            )
        except Exception:
            scrolled_up = False

        if not scrolled_up:
            logger.info(
                "get_conversation_messages: scroll-iter %d: top of chat "
                "reached (or scroll wedged); %d unique bubbles gathered",
                iter_n + 1, len(seen_keys),
            )
            break

        await _aio.sleep(settle_seconds)

    # Sort by absolute Y (top of chat = lowest Y = oldest first).
    ordered = sorted(seen_keys.values(), key=lambda t: t[0])
    handles = [el for _, el in ordered]
    return handles, dropped


def _resolve_orphan_bubble(
    msg: dict,
    prior_messages: list[dict],
) -> str:
    """Decide what to do with a bubble that has no resolvable sender.

    Some Upwork bubbles render with body content but no header — long
    messages get split into multiple sibling DOM bubbles where only
    the FIRST carries the story-header. Without this guard, the brain
    quotes the orphan with attribution "(no timestamp, no sender)" and
    dresses it up as authoritative contact content. Today's incident:
    James Blue's 10:37 PM spec list was split into a header bubble
    ("I want to give you the overal expectation of the program:") +
    a continuation bubble (the 30-line spec) — the second bubble was
    emitted with no sender/timestamp and quoted as orphan.

    Rule (in priority order):
      1. If the prior appended bubble is a CONTACT bubble (``is_mine``
         is False AND has a resolved ``sender``), concatenate the
         orphan body onto the prior bubble's content. Mutates the
         prior dict in-place.
      2. Otherwise drop the orphan (no append happens — caller must
         `continue` to skip the append). A warning is logged with a
         body preview so we can audit how often this fires.

    Returns ``"attached"`` or ``"dropped"``. Pure WRT ``msg`` (the
    orphan dict is never appended); only the prior contact bubble is
    mutated in the attach case. No exceptions.
    """
    body = (msg.get("content") or "").strip()
    if not body:
        logger.warning(
            "get_conversation_messages: dropping orphan bubble with "
            "no body and no sender",
        )
        return "dropped"
    if (
        prior_messages
        and prior_messages[-1].get("is_mine") is False
        and prior_messages[-1].get("sender")
    ):
        prior = prior_messages[-1]
        prior_body = (prior.get("content") or "").rstrip()
        prior["content"] = f"{prior_body}\n{body}" if prior_body else body
        logger.info(
            "get_conversation_messages: orphan bubble attached to "
            "prior contact bubble sender=%r ts=%r body_chars=%d",
            prior.get("sender"),
            prior.get("timestamp"),
            len(body),
        )
        return "attached"
    logger.warning(
        "get_conversation_messages: dropping orphan bubble with no "
        "sender and no foreign predecessor body_preview=%r",
        body[:80],
    )
    return "dropped"


async def _extract_message(
    el,
    *,
    last_sender: str | None = None,
    last_timestamp: str | None = None,
    last_is_mine: bool | None = None,
    me_name: str | None = None,
    contact_name: str | None = None,
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

    Carry-forward is SENDER-SCOPED — when ``last_is_mine`` is provided
    and the current bubble's ``is_mine`` class hint differs from it,
    the previous sender is the WRONG person to carry forward (this
    fired 2026-05-17 19:18: Upwork rendered Vato's reply without a
    visible story-header, so the parser inherited James's sender +
    timestamp from the prior bubble, and the brain ingested
    PropStream/Reonomy/Crexi as "James said this" — contaminating
    every downstream plan). When the flip is detected we DON'T carry
    forward the sender; instead we use `me_name` / `contact_name` as
    the contextual default for that side of the conversation.
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

    # is_mine via DOM class hint — needs to come BEFORE the carry-
    # forward decision so we can detect a sender flip and refuse to
    # carry the wrong author across.
    me_indicator = await el.query_selector(
        '.my-message, [data-test="my-message"], .sent, '
        '[class*="outgoing"], [class*="self"]'
    )
    current_is_mine = me_indicator is not None
    msg["is_mine"] = current_is_mine

    sender_flipped = (
        last_is_mine is not None and current_is_mine != last_is_mine
    )

    # Carry-forward author info when this bubble omits the header —
    # BUT only if the speaker hasn't flipped. On a flip, the previous
    # sender is by definition the WRONG person; fall back to me_name
    # (when this bubble is mine) or contact_name (when it isn't), and
    # leave timestamp blank rather than inheriting the prior block's.
    if not msg.get("sender"):
        if sender_flipped:
            if current_is_mine and me_name:
                msg["sender"] = me_name
            elif (not current_is_mine) and contact_name:
                msg["sender"] = contact_name
            # else: leave unset; caller may resolve via page heuristics
        elif last_sender:
            msg["sender"] = last_sender
    if not msg.get("timestamp") and last_timestamp and not sender_flipped:
        msg["timestamp"] = last_timestamp

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

    # P0a — same inbox-URL guard as get_conversation_messages. Refuse
    # to navigate when room_id is the bare inbox URL (would land on a
    # random open chat in the shared Brave profile).
    _validate_room_id(params.room_id)

    browser = get_browser()
    await browser.ensure_logged_in()

    # Navigate to conversation — same /ab/messages/rooms/ migration as above.
    if params.room_id.startswith("http"):
        url = params.room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{params.room_id}"

    # Post-nav room-id check inside safe_goto — refuses to silently
    # type a draft into the wrong conversation when Upwork's SPA
    # redirects an invalid room URL to /ab/messages.
    try:
        page = await browser.safe_goto(url)
    except UpworkRoomNotFound as exc:
        logger.warning(
            "send_message: room not found — requested=%s redirected_to=%s",
            exc.requested_url, exc.final_url,
        )
        return {
            "error": "room_not_found",
            "requested": exc.requested_url,
            "redirected_to": exc.final_url,
            "message": (
                "Refused to type message — Upwork redirected the room "
                "URL away from the requested conversation. The room id "
                "is invalid, deleted, or not accessible to this account. "
                "Call `upwork_get_messages` to list current rooms."
            ),
        }

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

    # P0a — same inbox-URL guard. Editing is even more sensitive than
    # sending: an inbox-URL nav would land on a random open chat and
    # the edit flow would target the wrong person's bubble entirely.
    _validate_room_id(params.room_id)

    browser = get_browser()
    await browser.ensure_logged_in()

    if params.room_id.startswith("http"):
        url = params.room_id
    else:
        url = f"https://www.upwork.com/ab/messages/rooms/{params.room_id}"

    # Post-nav room-id check — editing is even more sensitive than
    # sending, since an inbox-landing would expose YOUR previous bubbles
    # in whatever chat happens to be open in the shared Brave profile
    # to a potential hover+edit by a hover-triggered selector.
    try:
        page = await browser.safe_goto(url)
    except UpworkRoomNotFound as exc:
        logger.warning(
            "edit_message: room not found — requested=%s redirected_to=%s",
            exc.requested_url, exc.final_url,
        )
        return {
            "error": "room_not_found",
            "requested": exc.requested_url,
            "redirected_to": exc.final_url,
            "message": (
                "Refused to edit — Upwork redirected the room URL away "
                "from the requested conversation. The room id is "
                "invalid, deleted, or not accessible to this account. "
                "Call `upwork_get_messages` to list current rooms."
            ),
        }

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
