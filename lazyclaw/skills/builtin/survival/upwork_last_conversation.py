"""upwork_last_conversation — fetch + format the most recent Upwork chat.

Zero-LLM skill that the brain doesn't need to dispatch — wired into
``instant_dispatch`` so phrasings like "tell me what's in the last
conversation" / "show latest upwork message" route here in <10s
instead of through the brain's tool-search loop (which the
2026-05-16 incident showed costs ~2m33s + 4 wasted
``upwork_get_messages`` calls).

Pipeline:
  1. Call ``upwork_get_messages(limit=1)`` to find the most-recent
     conversation room.
  2. Read its ``contact_name`` for the ``me_name`` derivation hint
     and ``room_url`` (or fall back to the bare URL — the mcp-upwork
     bridge tolerates this and uses whichever upwork.com tab the
     user currently has open in their signed-in Brave).
  3. Call ``upwork_get_conversation(room_id=<url>, me_name=...)`` to
     pull the full thread.
  4. Format into a short Markdown summary the user can act on.

Works against the user's real signed-in Brave via the host bridge
(see ``lazyclaw/browser/host_bridge.py``) — exact same path the
2026-05-16 trace used successfully, just deterministic + cached.

Honors the user's stored ``display_name`` from ``SkillsProfile`` so
``is_mine`` flagging works (otherwise tolerates a None me_name and
falls back to the MCP's class-hint detection).
"""

from __future__ import annotations

import json
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class UpworkLastConversationSkill(BaseSkill):
    """Fetch + format the most recent Upwork conversation."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "upwork_last_conversation"

    @property
    def description(self) -> str:
        return (
            "Fetch a specific Upwork conversation thread and return its "
            "messages formatted as Markdown so you can read what the "
            "client actually said. **Call this FIRST before planning, "
            "scoping, estimating, or replying to any Upwork client** — "
            "never plan from memory of past turns. Pass `contact_name` "
            "to target a specific person ('James', 'James Blue'); omit "
            "to fetch the most recent thread. Zero LLM cost — calls "
            "upwork_get_messages + upwork_get_conversation "
            "deterministically. Drives the user's signed-in Brave via "
            "the host bridge."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "contact_name": {
                    "type": "string",
                    "description": (
                        "Optional. Match a specific client by display "
                        "name (case-insensitive substring + first-name "
                        "prefix). Examples: 'James', 'James Blue', "
                        "'sarah'. Omit to fetch the most recent thread "
                        "regardless of who it's with."
                    ),
                },
                "message_limit": {
                    "type": "integer",
                    "description": (
                        "Max messages to pull from the conversation. "
                        "Default 20. For planning a contract, pass 100 "
                        "to capture the full negotiation."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if self._registry is None:
            return "Error: skill registry not available."

        msg_limit = int(params.get("message_limit") or 20)
        contact_query = (params.get("contact_name") or "").strip() or None

        get_messages = self._find_tool("upwork_get_messages")
        get_conversation = self._find_tool("upwork_get_conversation")
        if get_messages is None or get_conversation is None:
            return (
                "Cannot fetch last Upwork conversation — mcp-upwork "
                "tools not registered. Make sure the upwork MCP is "
                "running and you're logged in to Upwork in your Brave."
            )

        # 1. Pull the inbox. limit=1 is fine when we just want "most
        # recent"; when matching a named contact we need a wider scan
        # since the target thread may not be at the top of the inbox.
        inbox_limit = 1 if contact_query is None else 50
        try:
            messages_raw = await get_messages.execute(
                user_id, {"limit": inbox_limit, "unread_only": False},
            )
        except Exception as exc:
            logger.exception("upwork_get_messages call raised")
            return f"Failed to fetch Upwork inbox: {exc}"

        blocked = _blocked_diagnosis(messages_raw)
        if blocked is not None:
            diagnosis = blocked.get("diagnosis") or "unknown"
            hint = (blocked.get("hint") or "").strip()
            page = blocked.get("page_url") or ""
            return (
                f"Upwork inbox read was BLOCKED, not empty "
                f"(diagnosis: {diagnosis}). {hint} Page: {page}. "
                "Do NOT report 'no conversations' — the data could not "
                "be read. Recovery: solve the wall in Brave, or drive "
                "the page directly with the `browser` tool "
                "(https://www.upwork.com/ab/messages/rooms/)."
            )

        messages = _normalize_inbox(messages_raw)
        if not messages:
            return (
                "No Upwork conversations found. Make sure you're logged "
                "in and have at least one chat thread."
            )

        if contact_query is not None:
            latest = _match_contact(messages, contact_query)
            if latest is None:
                preview = ", ".join(
                    (m.get("contact_name") or "(unknown)")
                    for m in messages[:8]
                )
                return (
                    f"No Upwork thread matches contact `{contact_query}`. "
                    f"Inbox top: {preview}. Try a different spelling, the "
                    "full name, or omit `contact_name` to see the most "
                    "recent thread."
                )
        else:
            latest = messages[0]
        # The 2026 URL parser may return the bare /rooms URL when the
        # room_id couldn't be extracted — that's fine, the MCP falls
        # back to the currently-open tab.
        room_id = (
            latest.get("room_url")
            or latest.get("room_id")
            or "https://www.upwork.com/ab/messages/rooms"
        )
        contact = latest.get("contact_name") or "(unknown contact)"

        # Pull the user's display_name for is_mine tagging — best effort
        me_name = await self._resolve_me_name(user_id)

        # 2. Fetch the full conversation
        try:
            conv_raw = await get_conversation.execute(user_id, {
                "room_id": room_id,
                "limit": msg_limit,
                "me_name": me_name,
            })
        except Exception as exc:
            logger.exception("upwork_get_conversation call raised")
            return (
                f"Could fetch inbox but failed on conversation with "
                f"{contact}: {exc}"
            )

        conv = _normalize_conversation(conv_raw)
        if not conv or not conv.get("messages"):
            return (
                f"Conversation with {contact} returned empty. The MCP may "
                "have lost the tab — open a chat in your Brave and retry."
            )

        return self._format_conversation(conv, fallback_contact=contact)

    # ── helpers ──────────────────────────────────────────────────────

    def _find_tool(self, suffix: str):
        """Locate an MCP-bridged tool by suffix match."""
        if self._registry is None:
            return None
        for tool_info in self._registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = (func.get("name") or "").lower()
            if tname.endswith(suffix.lower()) or suffix.lower() in tname:
                tool = self._registry.get(func.get("name", ""))
                if tool is not None:
                    return tool
        return self._registry.get(suffix)

    async def _resolve_me_name(self, user_id: str) -> str | None:
        """Resolve the user's own display name for ``is_mine`` tagging.

        Two sources, in priority order (best effort throughout):

          1. The stored ``SkillsProfile.display_name`` — the fast path,
             set once by the user via the ``set_display_name`` NL skill.
          2. **Fallback** — when the stored name is empty/None (user
             never set it, or ``sync_upwork_profile`` silently failed),
             pull the live name from the Upwork profile settings page via
             the ``upwork_get_my_profile`` MCP tool. Without this, an
             unset ``display_name`` yields ``me_name=None`` and the
             MCP's class-hint fallback (documented as always-False on the
             2026 layout) mis-tags EVERY one of the user's own bubbles as
             the contact — the exact sender-confabulation class this
             project fights.

        When BOTH sources come up empty we emit a LOUD warning so the
        unreliable attribution is diagnosable in logs.

        This method is ``async`` and is awaited from ``execute`` (which is
        itself ``async``), so calling the async MCP tool here is safe —
        no event loop is created or nested.
        """
        # 1. Stored profile display_name (fast path).
        try:
            from lazyclaw.survival.profile import get_profile
            profile = await get_profile(self._config, user_id)
            stored = getattr(profile, "display_name", None) or None
        except Exception:
            logger.debug("display_name lookup failed", exc_info=True)
            stored = None
        if stored:
            return stored

        # 2. Fallback: derive from the live Upwork profile. Best effort —
        # any failure leaves us at None and falls through to the warning.
        live = await self._resolve_me_name_from_live_profile(user_id)
        if live:
            logger.info(
                "upwork_last_conversation: display_name unset — derived "
                "me_name=%r from live Upwork profile (set_display_name to "
                "make this deterministic)",
                live,
            )
            return live

        logger.warning(
            "upwork_last_conversation: could NOT resolve me_name for user "
            "%s (stored display_name empty AND live-profile fallback "
            "missed) — sender attribution for this extraction is "
            "UNRELIABLE; the MCP class-hint fallback mis-tags the user's "
            "own messages as the contact. Run set_display_name or "
            "sync_upwork_profile to fix.",
            user_id,
        )
        return None

    async def _resolve_me_name_from_live_profile(
        self, user_id: str,
    ) -> str | None:
        """Best-effort live-profile name via ``upwork_get_my_profile``.

        Returns the scraped profile ``name`` (the same source
        ``sync_upwork_profile`` writes into ``display_name``) or None on
        any failure / missing tool / blank name.
        """
        get_my_profile = self._find_tool("upwork_get_my_profile")
        if get_my_profile is None:
            return None
        try:
            raw = await get_my_profile.execute(user_id, {})
        except Exception:
            logger.debug(
                "upwork_get_my_profile fallback raised", exc_info=True,
            )
            return None
        profile = _normalize_profile(raw)
        name = (profile.get("name") or "").strip()
        return name or None

    @staticmethod
    def _format_conversation(conv: dict, *, fallback_contact: str) -> str:
        """Render the conversation as a compact Markdown card.

        Format ordering matters for grounding (2026-05-24 audit):

        1. The MCP returns ``messages`` in chronological order
           (oldest-first by absolute Y), so the LAST element is the
           NEWEST message in the thread.
        2. The brain's F1 quote-then-summarize prompt instructs it to
           "quote the 3 most recent contact-side messages." Without an
           explicit callout, Opus 4.6/4.7 on prompt-cached contexts
           consistently picked the FIRST 3 contact-side messages
           instead — driven by cached paraphrases from prior session
           turns that (correctly at the time) called those "the
           latest." The F1 phase-2 content verifier observed this 8+
           times across May 21–24 ("3 unverified against fresh tool
           results") but only logs, doesn't block.
        3. Fix: prepend an UNAMBIGUOUS ``📌 MOST RECENT 3 MESSAGES
           FROM <contact>`` section that pre-extracts the last 3
           contact-side messages with NEWEST-FIRST ordering and
           explicit ``[NEWEST]`` markers. The brain quotes what's
           labeled, so cached confusion can't beat explicit labels.
           The full chronological transcript follows for context.
        """
        contact = conv.get("contact_name") or fallback_contact
        msgs = conv.get("messages") or []
        if not msgs:
            return f"Conversation with **{contact}**: (no messages)"

        lines: list[str] = [
            f"💬 **Last Upwork conversation — {contact}**",
            "",
        ]

        # 📌 MOST RECENT CONTACT-SIDE — pre-extracted, newest first,
        # unambiguously labeled so the brain can't pick the oldest end.
        contact_side = [
            m for m in msgs
            if not m.get("is_mine")
            and (m.get("content") or "").strip()
        ]
        latest_three = list(reversed(contact_side[-3:]))  # newest first
        if latest_three:
            lines.append(
                f"📌 **3 MOST RECENT MESSAGES FROM {contact}** "
                f"(newest first — quote these for any 'what did X say' "
                f"or 'most recent' question):"
            )
            lines.append("")
            for idx, m in enumerate(latest_three):
                ts = m.get("timestamp") or "(no timestamp)"
                content = (m.get("content") or "").strip()
                marker = "[NEWEST]" if idx == 0 else f"[#{idx + 1}]"
                lines.append(f"{marker} `{ts}` — **{contact}**:")
                for ln in content.splitlines():
                    lines.append(f"  {ln}")
                lines.append("")
            lines.append("---")
            lines.append("")

        # 📜 Full chronological transcript (oldest first) for context.
        lines.append(
            "📜 **Full chronological transcript** "
            "(oldest first; LAST entry = newest message in thread):"
        )
        lines.append("")
        for m in msgs[-30:]:  # cap rendered messages
            sender = m.get("sender") or ("(me)" if m.get("is_mine") else "(them)")
            ts = m.get("timestamp") or ""
            content = (m.get("content") or "").strip()
            if not content:
                continue
            arrow = "→" if m.get("is_mine") else "←"
            lines.append(f"`{ts}` {arrow} **{sender}**:")
            for ln in content.splitlines():
                lines.append(f"  {ln}")
            lines.append("")
        return "\n".join(lines).rstrip()


# ── Module-level normalizers (pure functions, easy to test) ─────────


def _match_contact(inbox: list[dict], query: str) -> dict | None:
    """Find the inbox entry whose `contact_name` best matches `query`.

    Tolerant lookup so the brain can pass either a first name ("James")
    or a full display name ("James Blue") and still hit the right
    thread. Two-pass match:

      1. Case-insensitive substring against the full `contact_name`.
      2. First-name prefix — "james" matches "James Blue" even if the
         brain dropped the surname.

    Returns the first hit (inboxes are newest-first, so the freshest
    matching thread wins). Returns None if no entry matches.
    """
    q = query.strip().lower()
    if not q:
        return None
    # Pass 1: substring anywhere in the display name.
    for item in inbox:
        name = (item.get("contact_name") or "").lower()
        if q in name:
            return item
    # Pass 2: first-token prefix match — "james" → "james blue"
    for item in inbox:
        name = (item.get("contact_name") or "").lower()
        first = name.split(maxsplit=1)[0] if name else ""
        if first.startswith(q):
            return item
    return None


def _blocked_diagnosis(raw) -> dict | None:
    """Detect mcp-upwork's ``empty_or_blocked`` structured result.

    Since 2026-06-10 the MCP read tools return
    ``{status: "empty_or_blocked", diagnosis, hint, page_url, ...}``
    instead of a bare ``[]`` when extraction yielded zero items —
    Cloudflare wall, login redirect, or selector drift. Returns that
    dict (unwrapped from a ``{"result": ...}`` envelope) so callers can
    surface the block instead of reporting a false empty inbox.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(raw, dict) and isinstance(raw.get("result"), dict):
        raw = raw["result"]
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if status == "empty_or_blocked":
        return raw
    # ``sidebar_unavailable`` (2026-07-26) is a ROUTING hint, not a dead
    # end: the rooms sidebar didn't render, but the row still carries
    # the room mined from the open conversation URL. This function runs
    # BEFORE _normalize_inbox, so claiming it here would return an error
    # string and skip the read entirely — exactly the bug being fixed.
    # Only degrade to "blocked" when there is nothing readable at all.
    if status == "sidebar_unavailable":
        if raw.get("room_id") or raw.get("room_url"):
            return None  # readable — let it flow to the normal path
        return raw
    return None


def _normalize_inbox(raw) -> list[dict]:
    """Coerce upwork_get_messages output into list[dict].

    A BARE single row (no envelope) must survive as a one-element list.
    2026-07-26: ``get_messages`` is typed ``-> list[dict] | dict`` and
    FastMCP emits one TextContent block per list item, so a ONE-item
    list arrives as ONE block — and ``MCPClient._call_tool_once`` only
    rebuilds a JSON array when ``len(parts) >= 2``. The sidebar-fallback
    row therefore reached this function as a bare object, hit
    ``raw.get("result") or raw.get("messages") or []`` → ``[]``, and the
    skill reported "No Upwork conversations found" while the thread was
    sitting right there (a direct ``upwork_get_conversation`` on the same
    room returned 20 real bubbles). That false negative reads as data
    rather than an error, and it drove an 11-minute retry loop.

    Fixed here at the CONSUMER rather than in ``mcp/client.py``:
    relaxing that ``>= 2`` guard would wrap every legitimate
    single-object MCP return in a spurious array and break other tools.

    Note ``contact_name`` is compared with ``is not None`` — the
    incident row had ``contact_name == ""``, which is falsy but REAL.
    A truthiness test here silently reintroduces the bug.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.debug("inbox response unparseable: %r", raw[:200])
            return []
    if isinstance(raw, dict):
        inner = raw.get("result") or raw.get("messages")
        if inner is not None:
            raw = inner
        elif "result" in raw or "messages" in raw:
            raw = []  # envelope present but empty — genuinely no rows
        elif raw.get("room_id") or raw.get("contact_name") is not None:
            raw = [raw]  # bare single row (sidebar fallback)
        else:
            raw = []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _normalize_profile(raw) -> dict:
    """Coerce upwork_get_my_profile output into a dict.

    The MCP returns either ``{name, title, ...}`` or a JSON string of
    same. Defensive against both shapes, the ``{"result": {...}}``
    envelope, and None — mirrors ``_normalize_conversation``.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(raw, dict) and "result" in raw and isinstance(raw["result"], dict):
        raw = raw["result"]
    if not isinstance(raw, dict):
        return {}
    return raw


def _normalize_conversation(raw) -> dict:
    """Coerce upwork_get_conversation output into a dict.

    The MCP returns either ``{room_id, contact_name, messages: [...]}``
    or a JSON string of same. Defensive against both shapes + None.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(raw, dict) and "result" in raw and isinstance(raw["result"], dict):
        raw = raw["result"]
    if not isinstance(raw, dict):
        return {}
    return raw
