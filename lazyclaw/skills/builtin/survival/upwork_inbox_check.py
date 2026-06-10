"""Upwork inbox sweeper — runs on a cron schedule.

Pulls unread DMs via mcp-upwork tools, classifies each against the
user's configured ``UpworkBotBehavior``, and either auto-replies (safe
categories) or escalates to the user via Telegram (sensitive categories).

Designed to be triggered by the heartbeat / a cron job: the user sets
the cron via ``set_upwork_bot_behavior(inbox_check_cron='0 9 * * *')``
and points an ``agent_jobs`` row at the instruction
"check upwork inbox now". The brain dispatches to this skill, which
runs entirely without further LLM hops if every message fits a
deterministic category. Categorisation is regex-first; the worker LLM
is only consulted for ambiguous messages (graceful Ollama-down
fallback returns 'unknown' which routes to escalation).
"""

from __future__ import annotations

import logging
import re

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ── Deterministic category rules ─────────────────────────────────────
# Each rule is (category, regex). First match wins. Patterns are
# intentionally conservative — anything not matched falls through to
# the LLM classifier (or 'unknown' on classifier failure), and
# 'unknown' is treated as ESCALATE by default.

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("admin_request", re.compile(
        r"\b(speak|talk|chat) (with|to) (the |your )?(owner|founder|manager|boss|admin|real person|actual person)\b",
        re.IGNORECASE,
    )),
    ("admin_request", re.compile(
        r"\b(who am i|are you a bot|are you human|is this a bot|are you ai|is this ai)\b",
        re.IGNORECASE,
    )),
    ("off_platform", re.compile(
        r"\b(slack|discord|whatsapp|signal|telegram|skype|zoom|teams|email me|let.?s connect on|move (this|us) to)\b",
        re.IGNORECASE,
    )),
    ("price_negotiation", re.compile(
        r"\b(can you (do (it|this)|drop) (for|to) \$?\d+|lower (the )?price|discount|cheaper|negotiate)\b",
        re.IGNORECASE,
    )),
    ("identity", re.compile(
        r"\b(show me your (id|passport|driver|driving)|video call|verify (your )?identity|prove who you are|see your face)\b",
        re.IGNORECASE,
    )),
    ("nda", re.compile(
        r"\b(nda|non.?disclosure|sign (this|an)? agreement|confidentiality agreement)\b",
        re.IGNORECASE,
    )),
    ("milestone_dispute", re.compile(
        r"\b(reject(ing)? (this )?milestone|milestone is wrong|not what i (asked|wanted)|dispute)\b",
        re.IGNORECASE,
    )),
    ("complaint", re.compile(
        r"\b(disappoint(ed|ing)|terrible|awful|poor quality|won.?t pay|refund)\b",
        re.IGNORECASE,
    )),
    ("review_or_feedback", re.compile(
        r"\b(leave (a |you a )?review|star (rating|review)|give (you|me) feedback)\b",
        re.IGNORECASE,
    )),
    ("acknowledge", re.compile(
        r"^\s*(thanks|thank you|thx|got it|ok|okay|sounds good|great|perfect|cool)\b[\s.!]*$",
        re.IGNORECASE,
    )),
    ("status_update", re.compile(
        r"\b(any update|how.?s it going|status|progress|when will|eta)\b",
        re.IGNORECASE,
    )),
    ("clarify_work", re.compile(
        r"\b(question about|can you clarify|what do you mean|which (one|format))\b",
        re.IGNORECASE,
    )),
    ("request_inputs", re.compile(
        r"\b(here.?s the|attached|sending you|sent you|please find)\b",
        re.IGNORECASE,
    )),
)


def _classify_deterministic(message_body: str) -> str | None:
    """First-pass classification via regex. Returns category or None."""
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(message_body):
            return category
    return None


def _classify_deadline(message_body: str) -> bool:
    """True if the message mentions a deadline shorter than 24h."""
    tight_patterns = (
        r"\b(in|within) \d+ ?(hours?|hrs?|h)\b",
        r"\b(today|tonight|asap|right now|urgent)\b",
        r"\bby (eod|end of day|tomorrow)\b",
    )
    for pat in tight_patterns:
        if re.search(pat, message_body, re.IGNORECASE):
            return True
    return False


def _blocked_inbox_result(messages) -> str | None:
    """Map mcp-upwork's ``empty_or_blocked`` structured read to a skill
    result, or ``None`` for a normal inbox payload.

    Cloudflare / login walls are LOUD (no ``[SILENT]`` prefix — the user
    must act in Brave); ambiguous selector-drift stays silent so the
    15-min cron doesn't spam, but never claims a verified-empty inbox.
    """
    if isinstance(messages, dict) and isinstance(messages.get("result"), dict):
        messages = messages["result"]
    if not (
        isinstance(messages, dict)
        and messages.get("status") == "empty_or_blocked"
    ):
        return None
    diagnosis = str(messages.get("diagnosis") or "unknown")
    hint = str(messages.get("hint") or "").strip()
    if diagnosis == "selector_drift_or_truly_empty":
        return (
            "[SILENT] Upwork inbox read returned no rooms (selector "
            "drift or truly empty) — unread state unverified."
        )
    return (
        f"⚠️ Upwork inbox check BLOCKED ({diagnosis}) — unread state "
        f"unknown, NOT an empty inbox. {hint}"
    ).strip()


class UpworkInboxCheckSkill(BaseSkill):
    """Sweep Upwork unread DMs, classify, auto-reply or escalate.

    Returns a structured summary the brain can post to Telegram or use
    to drive the next conversation turn.
    """

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "upwork_inbox_check"

    @property
    def description(self) -> str:
        return (
            "Sweep Upwork unread DMs, classify each against the user's "
            "configured UpworkBotBehavior, and either auto-reply (safe "
            "categories — acknowledge, status_update, clarify_work, "
            "request_inputs) or escalate to the user via Telegram "
            "(sensitive categories — admin_request, off_platform, "
            "price_negotiation, identity, nda, complaint, etc). "
            "Triggered by cron (default 9am daily) but also callable "
            "on demand: 'check my upwork inbox now'."
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
                "limit": {
                    "type": "integer",
                    "description": "Max unread conversations to process this run. Default 20.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "When true, classify and report but DO NOT send "
                        "any auto-reply or fire any escalation. Useful "
                        "for tuning category rules. Default false."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if self._registry is None:
            return "Error: skill registry not available."

        limit = int(params.get("limit") or 20)
        dry_run = bool(params.get("dry_run") or False)

        from lazyclaw.survival.upwork_bot import get_behavior
        from lazyclaw.survival.profile import get_profile
        behavior = await get_behavior(self._config, user_id)
        profile = await get_profile(self._config, user_id)

        # Locate the upwork MCP tools through the registry. Tool names
        # follow the `mcp_<server>_<tool>` convention from the bridge.
        get_messages = self._find_tool("upwork_get_messages")
        send_message = self._find_tool("upwork_send_message")
        if get_messages is None:
            return (
                "Cannot check Upwork inbox — mcp-upwork tools not "
                "registered. Make sure the upwork MCP is running and "
                "you're logged in to Upwork in your Brave."
            )

        try:
            messages_raw = await get_messages.execute(
                user_id, {"unread_only": True, "limit": limit},
            )
        except Exception as exc:
            return f"Failed to fetch Upwork messages: {exc}"

        # Tool result shape varies by adapter; normalize to a list of
        # dicts with at least `room_id` and `last_message`.
        if isinstance(messages_raw, str):
            try:
                import json as _json
                messages = _json.loads(messages_raw)
            except Exception:
                return f"Could not parse mcp-upwork response. Raw: {messages_raw[:500]}"
        else:
            messages = messages_raw
        blocked = _blocked_inbox_result(messages)
        if blocked is not None:
            return blocked
        if not isinstance(messages, list) or not messages:
            # [SILENT] prefix: heartbeat skips the Telegram push when the
            # result starts with this token. User asked for "no news = no
            # ping" so the every-15-min cron doesn't spam an empty inbox.
            # Foreground manual checks ("check my upwork inbox now") drop
            # the prefix in their final reply via the brain — only the
            # heartbeat-path callback honours it.
            return "[SILENT] Upwork inbox swept. No unread conversations."

        report_lines: list[str] = [f"Upwork inbox swept — {len(messages)} unread conversation(s):"]
        auto_replied = 0
        escalated = 0
        seen_unknown = 0

        for conv in messages:
            try:
                room_id = conv.get("room_id") or conv.get("url") or ""
                body = (
                    conv.get("last_message")
                    or conv.get("message")
                    or conv.get("text")
                    or ""
                )
                client = conv.get("client_name") or conv.get("name") or "(client)"
                body = str(body).strip()
                if not body or not room_id:
                    continue

                category = _classify_deterministic(body)
                deadline_tight = _classify_deadline(body)
                if deadline_tight and category not in (
                    "admin_request", "off_platform", "milestone_dispute",
                    "complaint", "nda",
                ):
                    category = "deadline_tight"
                if category is None:
                    category = "unknown"
                    seen_unknown += 1

                action = self._decide(category, behavior)
                preview = body[:100].replace("\n", " ")
                report_lines.append(
                    f"\n• {client}: {category} → {action}\n    \"{preview}\""
                )

                if dry_run:
                    continue

                if action == "auto_reply":
                    reply = self._compose_auto_reply(
                        category=category, body=body, profile=profile,
                        behavior=behavior, is_new_conversation=conv.get("is_new", False),
                    )
                    if send_message is not None and reply:
                        try:
                            await send_message.execute(
                                user_id, {"room_id": room_id, "message": reply},
                            )
                            auto_replied += 1
                        except Exception as exc:
                            report_lines.append(f"    auto-reply send failed: {exc}")
                elif action == "escalate":
                    urgency = (
                        "immediate" if deadline_tight else
                        behavior.escalation_urgency_default
                    )
                    suggested = self._suggest_replies(category)
                    # In notify_draft mode (default), compose a draft
                    # and put it FIRST in the suggested_replies list so
                    # the user sees "here's the bot's draft, Send /
                    # Edit / Skip" instead of just generic category
                    # templates. The draft uses the same composer as
                    # the auto-reply path — keeps behaviour consistent
                    # between "approve and send" and "auto-send".
                    if getattr(behavior, "reply_mode", "notify_draft") == "notify_draft":
                        try:
                            draft = self._compose_auto_reply(
                                category=category, body=body,
                                profile=profile, behavior=behavior,
                                is_new_conversation=conv.get("is_new", False),
                            )
                            if draft:
                                suggested = (draft,) + tuple(suggested)
                        except Exception:
                            logger.exception(
                                "Draft composition failed for category %s",
                                category,
                            )

                    # Deterministic awaited Telegram push FIRST — this
                    # is the guarantee the user asked for. Previously
                    # escalation was only fire-and-forget through
                    # escalate_to_human, and if its internal push_telegram
                    # silently failed (missing token, network blip),
                    # the user never knew a client replied. Now we
                    # push directly here, await the result, and on
                    # failure surface it in the inbox-sweep report.
                    push_ok = await self._push_escalation_alert(
                        client=client,
                        room_id=room_id,
                        room_url=conv.get("room_url", ""),
                        body=body,
                        category=category,
                        urgency=urgency,
                    )
                    if not push_ok:
                        report_lines.append(
                            "    ⚠️ Telegram push FAILED — check token + admin chat"
                        )

                    # Fire-and-forget escalate_to_human for the `/esc`
                    # reply-collection UX (suggested-replies menu,
                    # 6h pending window). The deterministic push above
                    # already alerted the user; this task just lets them
                    # respond inline. Yes, this means two Telegram
                    # messages per escalation — that's intentional. The
                    # alert is the "you must look at this NOW" ping;
                    # the escalate push is the "and here's the UI to
                    # reply" follow-up.
                    escalate = self._registry.get("escalate_to_human")
                    if escalate is not None:
                        try:
                            import asyncio as _aio
                            _aio.create_task(escalate.execute(user_id, {
                                "context": (
                                    f"Upwork client '{client}' (room {room_id}) said:\n"
                                    f"\"{body}\"\n\n"
                                    f"Classified as: {category}. Reply when ready."
                                ),
                                "suggested_replies": list(suggested),
                                "urgency": urgency,
                                "timeout_seconds": 6 * 3600,
                            }))
                            escalated += 1
                        except Exception as exc:
                            report_lines.append(f"    escalation failed: {exc}")
                    else:
                        # No escalate_to_human registered — the
                        # deterministic push above is the only
                        # notification path. Still count it as escalated
                        # if the push succeeded.
                        if push_ok:
                            escalated += 1
            except Exception as exc:
                logger.warning("Failed to process Upwork conv: %s", exc)

        report_lines.append(
            f"\nSummary: {auto_replied} auto-replied, {escalated} escalated, "
            f"{seen_unknown} unknown."
        )
        # No-news exemption: if every conversation was auto-replied (safe
        # categories the user approved) AND none escalated, the heartbeat
        # caller doesn't need to bother the user — the bot already handled
        # everything cleanly. Auto-replies always get logged inline in the
        # report body so the manual "what did you do?" surface still works,
        # but the Telegram push is suppressed.
        result = "\n".join(report_lines)
        if escalated == 0 and seen_unknown == 0:
            return f"[SILENT] {result}"
        return result

    # ── helpers ──────────────────────────────────────────────────────

    def _find_tool(self, suffix: str):
        """Locate a tool by suffix match (handles the mcp_ prefix bridge)."""
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

    def _decide(self, category: str, behavior) -> str:
        """Map (category, behavior) → 'auto_reply' | 'escalate' | 'skip'.

        Honors ``behavior.reply_mode``:
          - ``notify_draft`` (default): EVERY new message routes to
            escalate — the bot never sends without explicit user
            approval. Draft is composed and surfaced alongside the
            alert so the user can tap Send / Edit / Skip.
          - ``auto``: Bot may auto-send on safe categories
            (``auto_reply_categories``). Sensitive categories
            (``escalate_categories``) STILL go to escalate — auto
            mode does NOT override admin_request / identity /
            complaint / milestone_dispute / nda guardrails.
        """
        mode = getattr(behavior, "reply_mode", "notify_draft") or "notify_draft"
        if mode == "notify_draft":
            return "escalate"
        # auto mode: existing safe-vs-sensitive routing.
        if category in behavior.escalate_categories:
            return "escalate"
        if category in behavior.auto_reply_categories:
            return "auto_reply"
        # Unknown / unclassified → escalate (safer default).
        return "escalate"

    def _compose_auto_reply(
        self,
        *,
        category: str,
        body: str,
        profile,
        behavior,
        is_new_conversation: bool,
    ) -> str:
        """Build a deterministic safe auto-reply for the matched category.

        Avoids LLM cost on the high-volume safe path. The brain can
        always be invoked manually for richer replies via the regular
        chat flow.
        """
        signoff = profile.display_name or "the team"
        owner_offer = (
            "\n\nIf you'd prefer to speak with the founder directly, just say "
            "the word and I'll connect you."
            if is_new_conversation and behavior.intro_offer_owner_contact
            else ""
        )
        if category == "acknowledge":
            return f"Got it — thanks!\n\n— {signoff}{owner_offer}"
        if category == "status_update":
            return (
                f"Quick status: I'm on track. Will send a detailed update "
                f"with the next milestone.\n\n— {signoff}{owner_offer}"
            )
        if category == "clarify_work":
            return (
                f"Good question — I'll get you a clear answer in the next "
                f"reply. Drafting now.\n\n— {signoff}{owner_offer}"
            )
        if category == "request_inputs":
            return (
                f"Got it, received. I'll review and confirm the schema before "
                f"I start.\n\n— {signoff}{owner_offer}"
            )
        # Generic fallback — fires whenever the brain routed an
        # unmatched category to `auto_reply` (e.g., a custom category
        # the user added via set_upwork_bot_behavior). Previously this
        # returned "" which silently skipped the send. The fallback is
        # intentionally vague so it can't promise anything specific;
        # the user can NL-tune per-category templates if they want
        # richer replies.
        return (
            f"Got your message — thanks. I'll get back to you with a "
            f"detailed reply shortly.\n\n— {signoff}{owner_offer}"
        )

    async def _push_escalation_alert(
        self,
        *,
        client: str,
        room_id: str,
        room_url: str,
        body: str,
        category: str,
        urgency: str,
    ) -> bool:
        """Send a deterministic awaited Telegram alert for an escalated
        Upwork conversation.

        Returns True on successful push, False otherwise (caller surfaces
        the failure in the inbox-sweep report so it's visible).

        admin_request gets the loudest prefix because that's the single
        category where the client has explicitly asked for the human
        owner — missing it costs a real relationship.
        """
        from lazyclaw.notifications.push import push_telegram

        if category == "admin_request":
            prefix = "🚨 [Upwork — client asked to talk to YOU]"
        elif category in ("complaint", "milestone_dispute"):
            prefix = "⚠️ [Upwork — client unhappy]"
        elif category == "off_platform":
            prefix = "📵 [Upwork — client wants to move off-platform]"
        elif urgency == "immediate":
            prefix = "🚨 [Upwork — urgent reply needed]"
        else:
            prefix = "📨 [Upwork — needs your decision]"

        excerpt = body[:400].replace("\n", " ").strip()
        link = room_url or (
            f"https://www.upwork.com/ab/messages/rooms/{room_id}" if room_id else ""
        )
        text_lines = [
            f"{prefix}",
            f"From: {client}",
            f"Category: {category}",
            "",
            f'"{excerpt}"',
        ]
        if link:
            text_lines.extend(["", f"Open: {link}"])
        text = "\n".join(text_lines)

        try:
            return await push_telegram(self._config, text, parse_mode=None)
        except Exception as exc:
            logger.warning(
                "upwork inbox escalation push failed (%s): %s", category, exc
            )
            return False

    def _suggest_replies(self, category: str) -> tuple[str, ...]:
        """Suggested escalation replies tailored per category."""
        if category == "admin_request":
            return (
                "Of course — I'll have the founder reach out to you here shortly.",
                "I'm Vato's AI assistant. I can handle the technical side; happy to loop Vato in for any business decisions.",
            )
        if category == "off_platform":
            return (
                "I'd prefer to keep all communication on Upwork — it's the cleanest way to stay aligned on scope and milestones.",
                "Once we finish this milestone, happy to discuss other channels. For now, Upwork keeps everything tracked.",
            )
        if category == "price_negotiation":
            return (
                "Let me think about the scope and get back to you with a counter-offer.",
                "Happy to discuss — let's hop on a 15-min call to walk through the brief and find a number that works for both.",
            )
        if category == "identity":
            return (
                "I'd rather keep verification to Upwork's built-in flows for both our protection. Happy to add a video kickoff call once we're scoped.",
            )
        if category == "nda":
            return (
                "Send the NDA over and I'll review it before signing.",
                "I prefer Upwork's mutual NDA via the contract. Could we use that?",
            )
        if category == "milestone_dispute":
            return (
                "Sorry to hear that — what specifically should I change? I'll iterate.",
                "Let's hop on a 15-min call to walk through the deliverable line by line.",
            )
        if category == "complaint":
            return (
                "I hear you — let me make this right. What would resolution look like for you?",
            )
        return ()
