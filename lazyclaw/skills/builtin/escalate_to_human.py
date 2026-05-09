"""Generic escalation channel: agent ↔ human user via Telegram.

When the agent hits a situation it can't / shouldn't decide on its own
(off-scope client message, "I want to talk to your manager", price
negotiation, off-platform contact request, etc.), it calls this skill
instead of guessing.

Flow:
  1. Agent calls ``escalate_to_human(context=..., suggested_replies=[...])``
  2. Skill posts a structured Telegram push to the admin user
  3. Skill waits up to ``timeout_seconds`` (default 30 min) for the
     user to reply in Telegram
  4. Returns the user's reply text back to the calling agent

In-memory escalation registry: escalations are short-lived (minutes-
to-hours). Persistence across restarts is intentionally out of scope
for v1 — if the bot restarts mid-escalation, the user will see the
push and reply manually; the next inbox-check tick will pick up the
conversation again. Adding DB-backed persistence is a follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


@dataclass
class _PendingEscalation:
    """Live escalation awaiting user response."""
    escalation_id: str
    user_id: str
    context: str
    suggested_replies: tuple[str, ...]
    urgency: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    response: str = ""


# Module-level registry. Process-local (no cross-restart persistence).
# Keyed by escalation_id which is a UUID prefix to avoid collision with
# the user typing arbitrary text in Telegram.
_PENDING: dict[str, _PendingEscalation] = {}


def deliver_response(escalation_id: str, response: str) -> bool:
    """Wake a pending escalation with the user's reply.

    Called by the Telegram bot wiring (channels/telegram.py) when the
    user replies to an escalation push. Returns True if the escalation
    was found and woken; False if the ID is unknown (already resolved
    or never existed).
    """
    pending = _PENDING.get(escalation_id)
    if pending is None:
        return False
    pending.response = response
    pending.event.set()
    return True


def list_pending(user_id: str | None = None) -> list[dict]:
    """Return all currently-pending escalations. Used by the Web UI page
    AND by the Telegram /pending command for visibility."""
    out: list[dict] = []
    for esc in _PENDING.values():
        if user_id and esc.user_id != user_id:
            continue
        out.append({
            "escalation_id": esc.escalation_id,
            "context": esc.context[:200],
            "urgency": esc.urgency,
            "suggested_replies": list(esc.suggested_replies),
        })
    return out


_URGENCY_PREFIX = {
    "immediate":      "🚨 IMMEDIATE",
    "business_hours": "⚠️ Needs decision",
    "can_wait":       "ℹ️ Heads-up",
}


class EscalateToHumanSkill(BaseSkill):
    """Pause the agent loop and ask the human user to decide.

    Use whenever the next action would be irreversible, off-scope, or
    speak for the user on a sensitive topic (price, identity, off-
    platform contact, talk-to-manager, NDA, complaint, milestone
    rejection). NEVER guess on these — escalate.
    """

    def __init__(self, config: Any | None = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "escalate_to_human"

    @property
    def description(self) -> str:
        return (
            "Pause and ask the human user (admin / founder) to decide. "
            "Posts a structured Telegram message with context + suggested "
            "replies and waits for the user's response (up to 30 min by "
            "default). Returns the user's text decision so you can act on "
            "it. USE WHEN: client asks for the manager / owner / actual "
            "person; client wants to move off-platform (Slack, email, "
            "Discord, phone); client negotiates price; client asks for "
            "identity proof or NDA; client adds scope outside the brief; "
            "client gives a deadline <24h; client complains or rejects a "
            "milestone. NEVER guess on these. ALWAYS escalate."
        )

    @property
    def category(self) -> str:
        return "core"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": (
                        "What's happening. Include: the source channel "
                        "(Upwork DM, email, etc.), the client's exact "
                        "message verbatim, and why you can't decide on "
                        "your own. Be specific — the user reads this on "
                        "their phone and needs to grasp it in 5 seconds."
                    ),
                },
                "suggested_replies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Up to 4 short suggested reply options. The user "
                        "can pick one or write free text. Frame each "
                        "option as the FULL reply you'd send the client, "
                        "not a 1-word label."
                    ),
                },
                "urgency": {
                    "type": "string",
                    "enum": ["immediate", "business_hours", "can_wait"],
                    "description": (
                        "How fast the user should respond. immediate = "
                        "<1h deadline. business_hours = same-day. "
                        "can_wait = next time they check Telegram."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        "Max wait for user response. Default 1800 (30m). "
                        "On timeout the skill returns 'TIMEOUT' so you "
                        "can either retry or send a placeholder ack."
                    ),
                },
            },
            "required": ["context"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        context = (params.get("context") or "").strip()
        if not context:
            return "Error: context is required."
        suggested = params.get("suggested_replies") or []
        if not isinstance(suggested, list):
            suggested = []
        suggested = tuple(str(s).strip() for s in suggested if str(s).strip())[:4]

        urgency = params.get("urgency") or "business_hours"
        if urgency not in _URGENCY_PREFIX:
            urgency = "business_hours"

        timeout = int(params.get("timeout_seconds") or 1800)
        if timeout < 30:
            timeout = 30
        if timeout > 6 * 3600:
            timeout = 6 * 3600

        escalation_id = uuid4().hex[:10]
        pending = _PendingEscalation(
            escalation_id=escalation_id,
            user_id=user_id,
            context=context,
            suggested_replies=suggested,
            urgency=urgency,
        )
        _PENDING[escalation_id] = pending

        prefix = _URGENCY_PREFIX[urgency]
        body_lines = [f"{prefix} — escalation `{escalation_id}`", "", context]
        if suggested:
            body_lines.append("")
            body_lines.append("Suggested replies (reply with `/esc {id} <number>` or write your own):")
            for i, reply in enumerate(suggested, 1):
                body_lines.append(f"  {i}. {reply}")
            body_lines.append("")
            body_lines.append(f"Reply free text with: /esc {escalation_id} <your text>")
        else:
            body_lines.append("")
            body_lines.append(f"Reply with: /esc {escalation_id} <your text>")
        push_text = "\n".join(body_lines)

        try:
            from lazyclaw.notifications.push import push_telegram
            await push_telegram(self._config, push_text, parse_mode="Markdown")
        except Exception as exc:
            logger.warning("Escalation Telegram push failed: %s", exc)
            _PENDING.pop(escalation_id, None)
            return (
                f"Could not push escalation to Telegram ({exc}). "
                f"Falling back to inline ack — please decide on the spot."
            )

        try:
            await asyncio.wait_for(pending.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            _PENDING.pop(escalation_id, None)
            return (
                f"TIMEOUT after {timeout}s — user didn't respond. "
                f"Either retry the escalation or send a placeholder "
                f"acknowledgement to the client and try again later."
            )

        response = pending.response or ""
        _PENDING.pop(escalation_id, None)

        # If user picked a numeric option, expand to the full suggested reply
        stripped = response.strip()
        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(suggested):
                response = suggested[idx]

        return response or "(empty user response)"
