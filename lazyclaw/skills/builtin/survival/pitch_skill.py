"""Set the freelancer's value pitch via NL, polished by the worker LLM.

Pattern mirrors ``lazyclaw/tasks/smart_intake.py``:
- ROLE_WORKER (cheap, local Gemma 4 E2B by default)
- 5s hard timeout so the chat doesn't block
- Graceful fallback: if the polish call fails, save the raw user text
  unchanged so the user never loses their input.

Threaded into proposals via:
- ``draft_proposal_skill.py`` opener
- ``apply_skill.py`` LazyClaw branding block

The polished pitch is what every future proposal opens with — making
"the perfect pitch" a one-line NL command instead of a copy-paste task.
"""

from __future__ import annotations

import asyncio
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


_POLISH_PROMPT = """You polish a freelancer's value pitch. The user just told you, in their own words, what they offer to clients. Your job is to rewrite it as 2-3 sentences that:

- Lead with the concrete OUTCOME the client gets (not generic skills)
- Mention the platform / system the freelancer uses to deliver, if relevant
- Stay first-person, warm but professional
- NEVER add buzzwords ("synergy", "leverage", "cutting-edge") or fake credentials
- Keep length under 80 words
- Keep the freelancer's actual claims — do NOT invent skills, certifications, or experience they didn't mention
- Match the original language (English, Spanish, etc.)

Raw pitch from user:
\"\"\"{raw}\"\"\"

Return ONLY the polished pitch text. No quotes, no headers, no commentary."""


class SetFreelancePitchSkill(BaseSkill):
    """NL setter for the freelancer's polished value pitch."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_freelance_pitch"

    @property
    def description(self) -> str:
        return (
            "Set your freelance value pitch — the 'what I offer' line that "
            "opens every proposal. Pass natural language; the worker LLM "
            "polishes it once and saves both the raw + polished version. "
            "Examples: 'set my pitch to: I build Python automation backed by "
            "LazyClaw, my open-source AI agent platform' / "
            "'show my pitch' (no args = view)."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pitch": {
                    "type": "string",
                    "description": (
                        "Raw, natural-language pitch describing what you offer "
                        "to clients. Will be polished via the worker LLM "
                        "before saving."
                    ),
                },
                "skip_polish": {
                    "type": "boolean",
                    "description": (
                        "Skip the AI polish step and save the raw text exactly "
                        "as provided. Default false."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.profile import get_profile, update_profile

        raw = (params.get("pitch") or "").strip()
        if not raw:
            profile = await get_profile(self._config, user_id)
            current = profile.value_pitch.strip()
            if not current:
                return (
                    "No pitch set. Try: set_freelance_pitch pitch=\"I build "
                    "Python automation backed by LazyClaw — my open-source "
                    "AI agent platform — and ship under-1-month MVPs.\""
                )
            return f"Current pitch:\n\n{current}"

        if params.get("skip_polish"):
            await update_profile(self._config, user_id, {"value_pitch": raw})
            return f"Pitch saved (raw, not polished):\n\n{raw}"

        polished = await _polish_pitch(self._config, user_id, raw)
        if polished is None:
            await update_profile(self._config, user_id, {"value_pitch": raw})
            return (
                f"Worker LLM unavailable, saved raw pitch unchanged:\n\n{raw}\n\n"
                "Re-run later for the polished version, or pass skip_polish=true "
                "to silence this notice."
            )

        await update_profile(self._config, user_id, {"value_pitch": polished})
        return (
            f"Pitch polished and saved:\n\n{polished}\n\n"
            f"(raw was: {raw[:120]}{'...' if len(raw) > 120 else ''})"
        )


async def _polish_pitch(config, user_id: str, raw: str, timeout_s: float = 5.0) -> str | None:
    """Polish the pitch via the worker LLM. Returns None on any failure."""
    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter
    except Exception as exc:
        logger.debug("pitch_skill imports failed: %s", exc)
        return None

    prompt = _POLISH_PROMPT.format(raw=raw[:1000])

    try:
        paid = LLMRouter(config)
        eco = EcoRouter(config, paid)
        resp = await asyncio.wait_for(
            eco.chat(
                messages=[
                    LLMMessage(role="system", content="You polish freelance pitches. Return polished text only."),
                    LLMMessage(role="user", content=prompt),
                ],
                user_id=user_id,
                role=ROLE_WORKER,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.debug("pitch polish timed out after %.1fs", timeout_s)
        return None
    except Exception as exc:
        logger.debug("pitch polish failed: %s", exc)
        return None

    polished = (resp.content or "").strip()
    if not polished:
        return None
    if polished.startswith('"') and polished.endswith('"'):
        polished = polished[1:-1].strip()
    return polished or None
