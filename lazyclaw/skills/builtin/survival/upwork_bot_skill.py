"""NL setter for Upwork client-communication bot behavior.

Pattern mirrors ``set_skills_profile``: each field is an explicit
parameter, no LLM call needed for the setter itself. The LLM brain
is only involved in the inbox-check skill, where it classifies
incoming messages against the configured categories.

Examples (all NL-spoken):
  "check upwork messages every 2 hours"
  "auto-reply to acknowledge and clarify_work, escalate everything else"
  "turn off the founder-contact offer"
  "show my upwork bot settings"
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SetUpworkBotBehaviorSkill(BaseSkill):
    """View or update the Upwork client-communication bot config."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_upwork_bot_behavior"

    @property
    def description(self) -> str:
        return (
            "Configure how the Upwork client-communication bot behaves. "
            "Controls: how often to check the inbox, which message "
            "categories auto-reply vs escalate to user via Telegram, "
            "first-contact owner-offer behavior, default escalation "
            "urgency, tone override. NL examples: "
            "'check upwork messages every 6 hours' / "
            "'always escalate price negotiation' / "
            "'turn off the founder-contact offer' / "
            "'show my upwork bot settings' (no args = view)."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "inbox_check_cron": {
                    "type": "string",
                    "description": (
                        "Cron expression for the inbox-check schedule. "
                        "Default '0 9 * * *' = 9am daily. Examples: "
                        "'0 */2 * * *' = every 2h, '*/30 * * * *' = "
                        "every 30 min, '0 9,17 * * *' = 9am + 5pm daily."
                    ),
                },
                "intro_offer_owner_contact": {
                    "type": "boolean",
                    "description": (
                        "When true, the bot's first reply to a NEW client "
                        "conversation includes a one-line offer to connect "
                        "them with the founder if they'd like. Default true."
                    ),
                },
                "auto_reply_categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Message categories the bot can auto-reply to "
                        "without user approval. Defaults: acknowledge, "
                        "status_update, clarify_work, request_inputs."
                    ),
                },
                "escalate_categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Message categories that ALWAYS escalate to the "
                        "user via Telegram. Defaults: admin_request, "
                        "off_platform, price_negotiation, identity, nda, "
                        "off_scope, deadline_tight, complaint, "
                        "milestone_dispute, review_or_feedback."
                    ),
                },
                "escalation_urgency_default": {
                    "type": "string",
                    "enum": ["immediate", "business_hours", "can_wait"],
                    "description": (
                        "Default urgency tag for Telegram escalations "
                        "without an explicit deadline. Default 'business_hours'."
                    ),
                },
                "block_on_escalation": {
                    "type": "boolean",
                    "description": (
                        "True (default): bot waits for user's Telegram "
                        "response before sending ANY reply on escalated "
                        "messages — never speaks for the user without "
                        "permission. False: bot sends a placeholder "
                        "acknowledgement immediately and queues the user "
                        "decision for later."
                    ),
                },
                "tone_override": {
                    "type": "string",
                    "description": (
                        "Free-text tone override for bot replies. "
                        "Examples: 'be more casual', 'match the client's "
                        "language', 'always thank them first'. Empty = "
                        "use the user's value_pitch from SkillsProfile."
                    ),
                },
                "reply_mode": {
                    "type": "string",
                    "enum": ["notify_draft", "auto"],
                    "description": (
                        "How the bot handles incoming DMs. "
                        "'notify_draft' (default) = Telegram alert with "
                        "message + suggested draft; user taps Send/Edit/"
                        "Skip. 'auto' = bot sends draft autonomously "
                        "then notifies user AFTER; escalate_categories "
                        "(admin_request, identity, complaint, "
                        "milestone_dispute, etc.) STILL require human "
                        "approval. Aliases auto-accepted: 'auto_reply', "
                        "'auto_answer', 'draft', 'notify'."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.upwork_bot import (
            get_behavior, render_behavior, update_behavior,
        )

        raw_updates = {
            k: v
            for k, v in params.items()
            if v is not None and v != "" and v != []
        }

        if not raw_updates:
            behavior = await get_behavior(self._config, user_id)
            return render_behavior(behavior)

        try:
            await update_behavior(self._config, user_id, raw_updates)
        except ValueError as exc:
            return str(exc)

        # Cron-sync: when inbox_check_cron changes, also rewrite the
        # matching ``agent_jobs`` row so the heartbeat daemon picks
        # up the new schedule. Without this, only the encrypted
        # ``users.settings.survival.upwork_bot`` value updated and
        # the actual cron kept ticking on the old expression — which
        # is the bug the user hit the first time round.
        if "inbox_check_cron" in raw_updates:
            try:
                from lazyclaw.heartbeat.cron import is_valid
                from lazyclaw.heartbeat.orchestrator import list_jobs, update_job
                new_cron = str(raw_updates["inbox_check_cron"]).strip()
                if is_valid(new_cron):
                    jobs = await list_jobs(self._config, user_id)
                    for j in jobs:
                        if (j.get("name") or "").lower() in (
                            "survival_message_check", "upwork inbox check",
                            "upwork_inbox_check", "check upwork inbox",
                        ):
                            await update_job(
                                self._config, user_id, j["id"],
                                cron_expression=new_cron,
                            )
                            logger.info(
                                "Synced agent_jobs %s cron to %s",
                                j["id"], new_cron,
                            )
                            break
            except Exception:
                logger.exception(
                    "Failed to sync inbox_check_cron to agent_jobs row"
                )

        behavior = await get_behavior(self._config, user_id)
        return f"Updated.\n\n{render_behavior(behavior)}"
