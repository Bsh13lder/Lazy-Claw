"""request_user_approval — agent-callable checkpoint gate.

The agent uses this BEFORE any irreversible action: submitting a form,
sending an email, paying for an appointment, deleting data. The call
blocks until the user approves or rejects on the canvas / Telegram.

The same checkpoint name is auto-approved on subsequent calls in the
same session, so the agent can re-call inside a loop without re-prompting.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class RequestUserApprovalSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "request_user_approval"

    @property
    def description(self) -> str:
        return (
            "Pause and ask the user to approve before doing something risky "
            "or irreversible — submitting a form, paying, sending an email, "
            "booking an appointment, deleting data, etc. "
            "Returns once the user clicks Approve (or Reject). "
            "USE BEFORE: submit / pay / book / delete / sign / send. "
            "Do NOT use for read-only or low-stakes steps."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short label for what needs approval, e.g. "
                        "'Submit booking', 'Send invoice', 'Pay €40'. "
                        "Same label re-used in one session is auto-approved."
                    ),
                },
                "detail": {
                    "type": "string",
                    "description": "Optional 1-2 sentence context for the user.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "How long to wait before treating silence as reject. Default 600s.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.checkpoints import request_checkpoint

        name = (params or {}).get("name", "").strip()
        if not name:
            return "Error: 'name' parameter is required."
        detail = (params or {}).get("detail")
        timeout = int((params or {}).get("timeout_seconds") or 600)

        decision = await request_checkpoint(
            user_id=user_id, name=name, detail=detail, timeout=timeout,
        )
        if decision.get("approved"):
            reason = decision.get("reason") or ""
            suffix = f" ({reason})" if reason and reason != "auto-approved (previously confirmed)" else ""
            return f"User approved checkpoint '{name}'.{suffix} Continue."
        reason = decision.get("reason") or "rejected"
        return (
            f"User rejected checkpoint '{name}': {reason}. "
            "Stop the current action and ask the user how to proceed."
        )
