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


_PROMPT_TEMPLATE = """You are drafting a freelance proposal on behalf of a developer who runs LazyClaw — an open-source AI agent platform (MIT, Python, E2E encrypted). The developer's display name is {display_name}.

The developer's profile:
  Display name: {display_name}
  Title: {title}
  Skills: {skills}
  Bio: {bio}
  Value pitch (use as opener): {value_pitch}
  GitHub: {github_url}
  Min fixed rate: {min_fixed}

Job description (from the platform):
{description}

Write the proposal with this exact structure:

1. **Opener (2-3 lines):** Warm greeting using {display_name}. Use the value pitch above (lightly adapted to the specific job) — DO NOT replace it with a generic opener. Mention LazyClaw as your platform and why this specific job fits.

2. **"How we'd work" section (CRITICAL — be transparent):**
   - The day-to-day responder is a personal AI agent (LazyClaw)
   - The agent does the technical work: design, integration, scripts, tests
   - The HUMAN founder reviews and approves EVERY deliverable before it reaches the client — nothing ships without human sign-off
   - If the agent has questions or needs decisions, it escalates to the founder, who then contacts the client
   - Frame this as a STRENGTH using a 3-bullet list:
     ⚡ Fast responses (agent works 24/7, founder approves in business hours, Madrid timezone)
     ✅ Supervised quality — every deliverable passes human review
     💰 Competitive pricing — agent speed = lower cost, savings passed on

3. **"For your project I propose:" section:**
   - Numbered list (1, 2, 3, 4, 5) of concrete phases tailored to THIS job
   - Each phase = one short line with what's delivered
   - Show you understood the actual problem, not buzzwords

4. **"About me" section:**
   - If GitHub is set above, include it verbatim as proof of work. If empty, skip the link.
   - 1-2 lines on why LazyClaw stack (or relevant skills) fit this job
   - Stack relevance line (Python / FastAPI / browser automation / multi-channel / etc.)

5. **Next step:** Propose a 15-min discovery call to understand the project before quoting price. NEVER quote a fixed price in the proposal — always defer pricing until after the call.

6. **Sign-off:** "— {display_name}"

Rules:
- 250-400 words total (NOT 150 — be substantial, this is a credibility play)
- Bullet/numbered lists are ENCOURAGED (structure helps readability)
- Sparing emojis OK in the "How we'd work" bullets only (⚡ ✅ 💰)
- Bold key phrases with **markdown** (Upwork renders markdown in proposals)
- If the brief is in Spanish, write the ENTIRE proposal in Spanish (use Iberian Spanish: tú, "matrícula", "presupuesto"). Otherwise English.
- NO "I hope this message finds you well."
- NO "Dear Hiring Manager."
- NEVER quote a fixed price — always end with a discovery call
- NEVER promise to auto-submit anything

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
            display_name=profile.display_name or "the developer",
            title=profile.title or "Freelance developer",
            skills=", ".join(profile.skills) or "Python, automation, web",
            bio=profile.bio or "(none set)",
            value_pitch=profile.value_pitch or "(no pitch set — fall back to a 1-sentence opener built from skills)",
            github_url=profile.github_url or "(none set)",
            min_fixed=f"${profile.min_fixed_rate:.0f}" if profile.min_fixed_rate else "flexible",
            description=description[:3500],
        )

        try:
            response = await router.chat(
                messages=[LLMMessage(role="user", content=prompt)],
                user_id=user_id,
                max_tokens=1100,
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
