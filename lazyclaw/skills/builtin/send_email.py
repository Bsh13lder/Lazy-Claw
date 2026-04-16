"""Send email through n8n (LazyClaw's Gmail account).

Routes all outbound mail through the n8n 'LazyClaw Send Email' workflow
(webhook: /webhook/lazyclaw-send-email). The workflow uses n8n's Google
OAuth credential, so no SMTP config lives in LazyClaw.

The n8n base URL comes from the N8N_HOST env var (Docker default:
http://lazyclaw-n8n:5678). No API key needed — the webhook is public
inside the Docker network.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_DEFAULT_N8N_HOST = "http://lazyclaw-n8n:5678"
_WEBHOOK_PATH = "/webhook/lazyclaw-send-email"


class SendEmailSkill(BaseSkill):
    """Send an email via LazyClaw's Gmail account (routed through n8n)."""

    def __init__(self, config: Any = None, registry: Any = None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "send_email"

    @property
    def description(self) -> str:
        return (
            "Send an email from LazyClaw's Gmail account (via n8n). "
            "Use for all outgoing mail: notifications, confirmations, "
            "job applications. Returns the sent message ID on success."
        )

    @property
    def category(self) -> str:
        return "communication"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text body of the email",
                },
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        import httpx

        to = (params.get("to") or "").strip()
        subject = (params.get("subject") or "").strip()
        body = params.get("body") or ""

        if not to:
            return "Error: 'to' is required."
        if "@" not in to:
            return f"Error: '{to}' is not a valid email address."

        base = os.environ.get("N8N_HOST", _DEFAULT_N8N_HOST).rstrip("/")
        url = f"{base}{_WEBHOOK_PATH}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json={"to": to, "subject": subject, "body": body},
                    headers={"Content-Type": "application/json"},
                )
        except httpx.ConnectError as exc:
            logger.warning("n8n unreachable for send_email: %s", exc)
            return (
                "Email failed: cannot reach n8n. "
                "Is the lazyclaw-n8n container running?"
            )
        except httpx.TimeoutException:
            return "Email send timed out (>30s). Check the n8n workflow."

        if resp.status_code != 200:
            return (
                f"Email failed: n8n returned HTTP {resp.status_code}. "
                f"Check the 'LazyClaw Send Email' workflow in n8n UI."
            )

        try:
            data = resp.json()
        except Exception:
            data = {}

        # n8n returns plain `200 OK` with empty body when the workflow has
        # no Respond node reached (e.g. upstream node errored). Treat
        # empty-body 200 as failure so users don't get false confirmations.
        if not data:
            return (
                "Email may have failed: n8n returned an empty response. "
                "The most common cause is the 'Google account' credential "
                "in n8n not being authorized (no access token). "
                "Open n8n UI → Credentials → 'Google account' → "
                "click 'Connect with account' and grant Gmail send scope."
            )

        msg_id = data.get("messageId") or "(unknown)"
        return f"Email sent to {to}. Message ID: {msg_id}"
