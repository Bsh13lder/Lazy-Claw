"""Draft a tailored freelance proposal from a job URL.

Opens the job page (Upwork, Fiverr, Workana, Freelancer, etc.) via the
existing browser, reads the description, and asks the LLM to draft a
3-paragraph proposal that:
  - Leads with the specific problem the client described
  - References the user's skills from SkillsProfile
  - Ends with a concrete next-step question

NEVER auto-submits. Pushes the draft to Telegram; user reviews on phone,
copy-pastes into the platform, submits themselves.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """You are drafting a freelance proposal on behalf of a developer.

The developer's profile:
  Title: {title}
  Skills: {skills}
  Bio: {bio}
  Min fixed rate: {min_fixed}

Job description (from the platform):
{description}

Write a proposal in exactly 3 short paragraphs:
1. Show you understood the ACTUAL problem (paraphrase it, don't just list buzzwords).
2. State a concrete plan: what you'll deliver, how you'll approach it, roughly how long.
3. End with ONE specific clarifying question that proves you read the brief.

Rules:
- Under 150 words total.
- No "I hope this message finds you well."
- No bullet lists.
- No emojis.
- If the brief is in Spanish, write in Spanish. Otherwise English.
- Open with the specific technical detail, not with "I".

Return ONLY the proposal text, no headers, no footers."""


class DraftFreelanceProposalSkill(BaseSkill):
    """Open a job URL, extract description, draft a proposal via LLM."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "draft_freelance_proposal"

    @property
    def description(self) -> str:
        return (
            "Draft a tailored freelance proposal for a given job URL "
            "(Upwork, Fiverr, Workana, Freelancer, PeoplePerHour). "
            "Opens the page in the browser, reads the description, "
            "writes a 3-paragraph proposal matching the user's skills profile, "
            "pushes the draft to Telegram for review. "
            "NEVER auto-submits — the user copies the draft and submits it themselves."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "job_url": {
                    "type": "string",
                    "description": "Full URL of the job posting on the platform.",
                },
            },
            "required": ["job_url"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"

        job_url = (params.get("job_url") or "").strip()
        if not job_url.startswith("http"):
            return "Error: job_url must start with http(s)://"

        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            return "Error: browser skill not available."

        # Open the page and read it — reuses existing browser skill
        try:
            await browser.execute(user_id, {"action": "open", "url": job_url})
            page_text = await browser.execute(user_id, {"action": "read"})
        except Exception as exc:
            logger.warning("Could not open job URL: %s", exc)
            return f"Could not open {job_url}: {exc}"

        if not page_text or len(page_text) < 100:
            return (
                f"Page at {job_url} returned no readable content "
                "(may require login or Cloudflare challenge)."
            )

        # Trim to the chunk most likely to contain the brief
        description = _extract_description(page_text)

        # Load user profile
        from lazyclaw.survival.profile import get_profile
        profile = await get_profile(self._config, user_id)

        # Draft via LLM router
        try:
            from lazyclaw.llm.providers.base import LLMMessage
            from lazyclaw.llm.router import LLMRouter
            router = LLMRouter(self._config)
        except Exception as exc:
            logger.warning("LLMRouter unavailable: %s", exc)
            return f"Could not load LLM router: {exc}"

        prompt = _PROMPT_TEMPLATE.format(
            title=profile.title or "Freelance developer",
            skills=", ".join(profile.skills) or "Python, automation, web",
            bio=profile.bio or "(none set)",
            min_fixed=f"${profile.min_fixed_rate:.0f}" if profile.min_fixed_rate else "flexible",
            description=description[:3500],
        )

        try:
            response = await router.chat(
                messages=[LLMMessage(role="user", content=prompt)],
                user_id=user_id,
                max_tokens=500,
                temperature=0.7,
            )
            draft = (response.content or "").strip()
        except Exception as exc:
            logger.warning("Proposal drafting failed: %s", exc)
            return f"Could not draft proposal: {exc}"

        if not draft:
            return "LLM returned empty proposal. Try again."

        # Push to Telegram — user reviews on phone and copy-pastes
        try:
            from lazyclaw.notifications.push import push_telegram
            tg_text = (
                f"📝 *Proposal draft*\n{job_url}\n\n"
                f"```\n{draft}\n```\n\n"
                "_Copy → paste into the platform → submit yourself. "
                "Never auto-submit (permanent-ban risk)._"
            )
            await push_telegram(self._config, tg_text)
        except Exception as exc:
            logger.debug("Telegram push for proposal skipped: %s", exc)

        return (
            f"Drafted proposal for {job_url}\n\n"
            f"{draft}\n\n"
            "Review on Telegram and paste into the platform when ready."
        )


def _extract_description(page_text: str) -> str:
    """Pull the densest middle block of the page — usually the job brief."""
    text = page_text.strip()
    if len(text) <= 3500:
        return text
    # Skip first ~500 chars of nav junk, return middle 3500
    start = min(500, len(text) // 4)
    return text[start:start + 3500]
