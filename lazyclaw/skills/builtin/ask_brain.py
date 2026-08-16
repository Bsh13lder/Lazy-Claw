"""ask_brain — stuck-worker escalation: worker → brain → user.

A specialist that is genuinely stuck mid-task (repeated failures, ambiguous
instructions, a fork it cannot resolve from the page or its tools) asks the
BRAIN for one decisive instruction instead of thrashing. The brain answers
from the task context; when the question is genuinely the USER's to decide
(missing personal data, an irreversible choice, a preference), the brain
replies with an ``ASK_USER:`` marker and the question is escalated through
the existing checkpoint plumbing — whose approve/reject decision already
carries a free-text ``reason``, so the user's typed answer flows straight
back into the worker's loop as the tool result.

Escalation chain (each step falls through to the next on failure):
  1. brain consult (role="brain", no tools, one decisive answer)
  2. user checkpoint (approve/reject + optional typed note)
  3. timeout → "proceed with the safest non-destructive step" instruction
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Question checkpoints wait shorter than action checkpoints: the worker's
# own budget (600s background / 480s sync-browser floor) must outlive the
# wait, or the parent kills the worker while it's still "asking".
QUESTION_TIMEOUT_SECONDS = 240

_ASK_USER_MARKER = "ASK_USER:"

_CONSULT_SYSTEM = (
    "You are the team lead of the user's personal assistant. Specialists "
    "operate ONLY the user's own authorized accounts, sites and systems on "
    "the user's explicit instructions. One of your specialists is STUCK "
    "mid-task and asks for guidance. Reply with ONE decisive instruction "
    "(2-5 sentences, no hedging) the specialist can act on immediately.\n"
    "ONLY if the question genuinely requires the user's own decision — "
    "missing personal data, an irreversible/costly choice, or a personal "
    "preference — reply with exactly:\n"
    f"{_ASK_USER_MARKER} <one short question for the user>\n"
    "Never use the marker for anything the specialist could try itself."
)

_TIMEOUT_INSTRUCTION = (
    "User unavailable (question timed out). Do NOT take irreversible "
    "actions. Take the safest non-destructive step available, or stop and "
    "report exactly what you accomplished and where you are stuck."
)


class AskBrainSkill(BaseSkill):
    """Worker-callable escalation tool (Claude Code dispatcher tactic)."""

    def __init__(self, config=None, eco_router=None):
        self._config = config
        self._eco_router = eco_router

    @property
    def category(self) -> str:
        return "team"

    @property
    def name(self) -> str:
        return "ask_brain"

    @property
    def description(self) -> str:
        return (
            "STUCK mid-task? Ask the team lead (brain) for one decisive "
            "instruction. Use after 2+ failed approaches, or when the task "
            "is ambiguous, or at a fork you cannot resolve from the page or "
            "your tools. If only the USER can decide, the question is "
            "forwarded to them and their answer is returned. Do NOT use for "
            "anything you can figure out yourself — it pauses the task."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The ONE question you are stuck on.",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "What you tried and where you are (2-4 sentences). "
                        "The brain only sees this — be concrete."
                    ),
                },
            },
            "required": ["question"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        question = ((params or {}).get("question") or "").strip()
        if not question:
            return "Error: 'question' parameter is required."
        context = ((params or {}).get("context") or "").strip()

        guidance = await self._consult_brain(user_id, question, context)
        if guidance is not None and not guidance.startswith(_ASK_USER_MARKER):
            return f"BRAIN GUIDANCE: {guidance}"

        if guidance is not None:
            user_question = guidance[len(_ASK_USER_MARKER):].strip() or question
        else:
            # Brain unavailable — the user is the only escalation left.
            user_question = question
        return await self._ask_user(user_id, user_question)

    async def _consult_brain(
        self, user_id: str, question: str, context: str,
    ) -> str | None:
        """One brain-role consult. Returns None when the brain is unreachable."""
        if self._eco_router is None:
            return None
        from lazyclaw.llm.providers.base import LLMMessage

        body = f"SPECIALIST QUESTION: {question}"
        if context:
            body += f"\n\nWHERE I AM / WHAT I TRIED: {context}"
        try:
            response = await self._eco_router.chat(
                [
                    LLMMessage(role="system", content=_CONSULT_SYSTEM),
                    LLMMessage(role="user", content=body),
                ],
                user_id,
                role="brain",
            )
            text = (response.content or "").strip()
            return text or None
        except Exception as exc:
            logger.warning("ask_brain consult failed, escalating to user: %s", exc)
            return None

    async def _ask_user(self, user_id: str, question: str) -> str:
        from lazyclaw.browser.checkpoints import request_checkpoint

        decision = await request_checkpoint(
            user_id=user_id,
            # Question text in the name keeps distinct questions from
            # auto-approving each other (checkpoints remember by name).
            name=f"question: {question[:60]}",
            detail=question,
            timeout=QUESTION_TIMEOUT_SECONDS,
        )
        reason = (decision.get("reason") or "").strip()
        if decision.get("approved"):
            answer = reason if reason and "auto-approved" not in reason else (
                "Yes — proceed with your suggested approach."
            )
            return f"USER ANSWERED: {answer}"
        if "timed out" in reason:
            return _TIMEOUT_INSTRUCTION
        return (
            f"USER SAYS: {reason or 'No — do not proceed.'} "
            "Follow this instruction over anything else."
        )
