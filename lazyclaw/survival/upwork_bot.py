"""Upwork client-communication bot behavior config.

Stored in users.settings JSON under "survival.upwork_bot", same pattern
as ``survival.profile``. Every field is NL-editable via the
``set_upwork_bot_behavior`` skill.

The bot uses these settings when handling Upwork DMs:
  - ``inbox_check_cron`` — heartbeat schedule for the inbox poller
  - ``auto_reply_categories`` / ``escalate_categories`` — per-message
    classification rules
  - ``intro_offer_owner_contact`` — first-contact behavior (offer to
    connect the client with the founder if they ask)
  - ``escalation_urgency_default`` — Telegram push urgency for non-
    deadline-tagged escalations
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session


# ── Default categories ───────────────────────────────────────────────
# Every incoming Upwork message gets classified into one of these by
# the inbox-check skill. The user can NL-move categories between
# auto-reply and escalate buckets without code changes.

AUTO_REPLY_SAFE_DEFAULT: tuple[str, ...] = (
    "acknowledge",         # "got it, working on it"
    "status_update",       # "hour 4 done, week 1 milestone delivered"
    "clarify_work",        # "JSON or YAML for the schema?"
    "request_inputs",      # "please send the transcript file + brand template"
)

ESCALATE_DEFAULT: tuple[str, ...] = (
    "admin_request",       # "I want to talk to your manager / the actual person"
    "off_platform",        # "let's continue on Slack / Discord / email / phone"
    "price_negotiation",   # "can you do this for $400 instead?"
    "identity",            # "show me your ID / your real name / a video call"
    "nda",                 # "sign this NDA before we continue"
    "off_scope",           # "while you're at it can you also do X?" outside original brief
    "deadline_tight",      # "I need this in 6 hours" — anything <24h
    "complaint",           # "this isn't what I wanted, I'm disappointed"
    "milestone_dispute",   # "I'm rejecting milestone 1"
    "review_or_feedback",  # client wants to give star rating / review
)


@dataclass(frozen=True)
class UpworkBotBehavior:
    """Immutable Upwork client-communication settings."""

    # How often the heartbeat should fire the inbox check.
    # Cron format. Default */15 * * * * = every 15 minutes.
    # Was "0 9 * * *" (daily 9am) — too rare, real client replies were
    # missed for ~24h until the next morning's poll. Tight cadence is
    # cheap because the inbox check is regex-first (zero LLM cost on
    # empty/safe sweeps; the worker LLM only runs on the unknown path).
    # NL: "check upwork messages every 2 hours" → "0 */2 * * *".
    inbox_check_cron: str = "*/15 * * * *"

    # When True, the bot's first reply to a NEW conversation includes a
    # one-line offer: "If you'd like to speak with the founder directly,
    # just say the word and I'll connect you." Lets the client opt in
    # rather than the bot pretending to be human.
    intro_offer_owner_contact: bool = True

    # Categories the bot is allowed to auto-reply to. Anything else
    # routes to escalation.
    auto_reply_categories: tuple[str, ...] = AUTO_REPLY_SAFE_DEFAULT

    # Categories that ALWAYS escalate to the user via Telegram.
    # These override auto-reply even if a category appears in both.
    escalate_categories: tuple[str, ...] = ESCALATE_DEFAULT

    # Default urgency tag for Telegram escalations when the message
    # itself doesn't carry an explicit deadline. One of: "immediate",
    # "business_hours", "can_wait".
    escalation_urgency_default: str = "business_hours"

    # When True, the bot waits for the user's Telegram response before
    # sending ANY reply on escalated messages. When False, the bot
    # acknowledges the client ("noted, getting back to you shortly")
    # immediately and queues the user response. Default True — never
    # speak for the user without permission on sensitive topics.
    block_on_escalation: bool = True

    # Free-text override for the bot's reply tone. NL: "be more
    # casual" / "be more formal" / "match the client's language".
    # Empty = use the user's value_pitch from SkillsProfile.
    tone_override: str = ""

    # Reply mode controls how the bot handles incoming Upwork DMs:
    #   "notify_draft" (default) — Telegram alert with message + a
    #       suggested draft. User taps Send / Edit / Skip. Bot NEVER
    #       speaks for the user without explicit approval.
    #   "auto" — Bot sends the drafted reply autonomously, then
    #       Telegram-notifies the user AFTER (post-hoc) so they see
    #       what was said. Categories in ``escalate_categories``
    #       still force human approval (admin_request, identity,
    #       complaint, milestone_dispute, etc.) — auto mode does NOT
    #       override those guardrails. Used when the user wants the
    #       bot to handle the inbox unattended (price negotiation,
    #       status replies, scope clarifications).
    # NL: "switch upwork to auto reply" / "turn off auto reply mode".
    reply_mode: str = "notify_draft"


_DEFAULT_BEHAVIOR = UpworkBotBehavior()
_BEHAVIOR_FIELDS = frozenset(UpworkBotBehavior.__dataclass_fields__.keys())
_TUPLE_FIELDS: frozenset[str] = frozenset({
    "auto_reply_categories", "escalate_categories",
})
_BOOL_FIELDS: frozenset[str] = frozenset({
    "intro_offer_owner_contact", "block_on_escalation",
})


_VALID_REPLY_MODES: frozenset[str] = frozenset({"notify_draft", "auto"})


def _coerce_updates(updates: dict[str, object]) -> dict[str, object] | str:
    """Validate and coerce update values. Returns error string on failure."""
    result: dict[str, object] = {}
    for k, v in updates.items():
        if k not in _BEHAVIOR_FIELDS:
            continue
        if k in _BOOL_FIELDS:
            if isinstance(v, bool):
                result[k] = v
            elif isinstance(v, str):
                result[k] = v.strip().lower() in ("true", "yes", "y", "1", "on")
            else:
                return f"Invalid value for {k}: must be true/false."
        elif k in _TUPLE_FIELDS:
            if isinstance(v, (list, tuple)):
                result[k] = tuple(str(x).strip().lower() for x in v if str(x).strip())
            else:
                return f"Invalid value for {k}: must be a list of category names."
        elif k == "reply_mode":
            # Tolerant aliases so NL maps cleanly:
            #   "auto" / "auto_reply" / "auto_answer" → "auto"
            #   "draft" / "notify" / "notify_draft" / "manual" → "notify_draft"
            raw = str(v).strip().lower().replace("-", "_").replace(" ", "_")
            if raw in {"auto", "auto_reply", "auto_answer", "autonomous"}:
                result[k] = "auto"
            elif raw in {
                "notify_draft", "draft", "notify",
                "manual", "approve", "review",
            }:
                result[k] = "notify_draft"
            else:
                return (
                    f"Invalid reply_mode: '{v}'. Use 'notify_draft' "
                    f"(default — bot drafts, user approves) or 'auto' "
                    f"(bot sends, still escalates sensitive categories)."
                )
        else:
            result[k] = str(v)
    return result


async def get_behavior(config: Config, user_id: str) -> UpworkBotBehavior:
    """Load Upwork bot behavior from encrypted settings."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    if not row or not row[0]:
        return _DEFAULT_BEHAVIOR
    try:
        settings = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return _DEFAULT_BEHAVIOR
    data = settings.get("survival", {}).get("upwork_bot", {})
    return _behavior_from_data(data)


async def update_behavior(
    config: Config, user_id: str, updates: dict[str, object]
) -> UpworkBotBehavior:
    """Update Upwork bot behavior atomically. Returns new immutable config."""
    coerced = _coerce_updates(updates)
    if isinstance(coerced, str):
        # Validation error — surface it back as a behavior with no changes
        # would be confusing. Caller should branch on this string.
        raise ValueError(coerced)
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        settings = json.loads(row[0]) if row and row[0] else {}
        current = _behavior_from_data(
            settings.get("survival", {}).get("upwork_bot", {})
        )
        merged: dict[str, object] = {}
        for k, v in dataclasses.asdict(current).items():
            merged[k] = list(v) if isinstance(v, tuple) else v
        for k, v in coerced.items():
            merged[k] = list(v) if isinstance(v, tuple) else v
        settings.setdefault("survival", {})["upwork_bot"] = merged
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(settings), user_id),
        )
        await db.commit()
    return await get_behavior(config, user_id)


def _behavior_from_data(data: dict) -> UpworkBotBehavior:
    if not data:
        return _DEFAULT_BEHAVIOR
    cleaned: dict[str, object] = {}
    for k, v in data.items():
        if k not in _BEHAVIOR_FIELDS:
            continue
        if k in _TUPLE_FIELDS and isinstance(v, list):
            cleaned[k] = tuple(v)
        else:
            cleaned[k] = v
    return UpworkBotBehavior(**cleaned)


def render_behavior(behavior: UpworkBotBehavior) -> str:
    """Human-readable summary of the current behavior."""
    mode_label = {
        "notify_draft": "notify + draft (user approves each reply)",
        "auto": "auto-reply (bot sends, escalations still ask first)",
    }.get(behavior.reply_mode, behavior.reply_mode)
    return (
        f"Upwork Bot Behavior:\n"
        f"  Reply mode: {mode_label}\n"
        f"  Inbox check: {behavior.inbox_check_cron}\n"
        f"  First-contact owner offer: "
        f"{'on' if behavior.intro_offer_owner_contact else 'off'}\n"
        f"  Auto-reply: {', '.join(behavior.auto_reply_categories) or 'none'}\n"
        f"  Escalate: {', '.join(behavior.escalate_categories) or 'none'}\n"
        f"  Block on escalation: "
        f"{'yes — wait for user before any reply' if behavior.block_on_escalation else 'no — bot sends placeholder ack'}\n"
        f"  Default urgency: {behavior.escalation_urgency_default}\n"
        f"  Tone override: {behavior.tone_override or '(use value_pitch)'}"
    )
