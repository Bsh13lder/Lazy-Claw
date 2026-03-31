"""Apply to a freelance job with LazyClaw-branded cover letter."""

from __future__ import annotations

import json
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_SEARCH_PREFIX = "SURVIVAL_SEARCH:"

# LazyClaw branding prompt injected into cover letter generation
_LAZYCLAW_BRANDING = (
    "IMPORTANT CONTEXT: You are writing this proposal on behalf of LazyClaw, "
    "an AI-powered agent platform. Be transparent about this:\n"
    "- LazyClaw is an AI agent that executes work autonomously with tools, "
    "browser control, and code execution capabilities\n"
    "- A human founder reviews ALL deliverables before submission (quality gate)\n"
    "- 24/7 availability, fast turnaround, automated testing\n"
    "- Professional grade, E2E encrypted workspace\n"
    "- Frame this as a STRENGTH: AI precision + human oversight = best of both worlds\n\n"
)


class ApplyJobSkill(BaseSkill):
    """Apply to a freelance job with a personalized cover letter."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "apply_job"

    @property
    def description(self) -> str:
        return (
            "Apply to a freelance job with a personalized cover letter. "
            "LazyClaw applies as an AI agent — transparent to clients. "
            "You MUST approve before it's submitted. "
            "Usage: 'apply to job 1' or 'apply to Build REST API job'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "job_reference": {
                    "type": "string",
                    "description": "Job number from search results, title, or URL",
                },
                "custom_note": {
                    "type": "string",
                    "description": "Optional custom note to include in the proposal",
                },
            },
            "required": ["job_reference"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.memory.personal import search_memories
        from lazyclaw.survival.gig import list_gigs, update_gig_status
        from lazyclaw.survival.platforms import BROWSER_PLATFORMS
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)
        ref = params.get("job_reference", "")
        custom_note = params.get("custom_note", "")

        # Find last search results from memory
        memories = await search_memories(
            self._config, user_id, _SEARCH_PREFIX, limit=5,
        )

        if not memories:
            return "No recent job search. Use 'search jobs' first."

        latest = memories[0]
        content = latest["content"]
        json_str = content[len(_SEARCH_PREFIX):]
        try:
            jobs = json.loads(json_str)
        except json.JSONDecodeError:
            return "Could not parse last search results. Search again."

        # Match by number or title
        job = None
        if ref.isdigit():
            idx = int(ref) - 1
            if 0 <= idx < len(jobs):
                job = jobs[idx]
        else:
            ref_lower = ref.lower()
            for j in jobs:
                if ref_lower in j.get("title", "").lower():
                    job = j
                    break

        if job is None:
            return f"Job '{ref}' not found in recent search results. Search again?"

        # Generate cover letter with LazyClaw branding
        letter = await self._generate_letter(user_id, job, profile, custom_note)

        # Update gig record to 'applied'
        found_gigs = await list_gigs(self._config, user_id, status="found")
        for gig in found_gigs:
            if gig.title == job.get("title") or gig.url == job.get("url"):
                await update_gig_status(
                    self._config, user_id, gig.id, "applied",
                    proposal_text=letter,
                )
                break

        # Browser platforms: fill the proposal form directly
        if job.get("platform", "").lower() in BROWSER_PLATFORMS:
            return await self._apply_via_browser(user_id, job, profile, letter)

        # Non-browser platforms: show letter + URL
        return (
            f"Cover letter for: **{job['title']}** ({job.get('platform', 'N/A')})\n"
            f"Budget: {job.get('budget', 'N/A')}\n\n"
            f"---\n{letter}\n---\n\n"
            f"URL: {job.get('url', 'N/A')}\n\n"
            f"Open the link and submit manually, or say 'submit' to apply via browser."
        )

    async def _apply_via_browser(
        self, user_id: str, job: dict, profile, letter: str,
    ) -> str:
        """Fill Upwork proposal form via browser. Does NOT click Submit."""
        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            return (
                f"Cover letter ready but browser not available.\n\n"
                f"---\n{letter}\n---\n\n"
                f"Apply manually at: {job.get('url', 'N/A')}"
            )

        job_url = job.get("url", "")
        if not job_url:
            return "No job URL found. Search for jobs again."

        try:
            await browser.execute(user_id, {"action": "open", "url": job_url})
            await browser.execute(user_id, {"action": "read"})
            await browser.execute(user_id, {
                "action": "click", "target": "Submit a Proposal",
            })
            await browser.execute(user_id, {"action": "read"})
            await browser.execute(user_id, {
                "action": "type", "target": "Cover Letter", "text": letter,
            })

            if profile.min_hourly_rate > 0:
                await browser.execute(user_id, {
                    "action": "type",
                    "target": "Hourly Rate",
                    "text": str(profile.min_hourly_rate),
                })

            return (
                f"Proposal ready on Upwork for: **{job['title']}**\n\n"
                f"---\n{letter}\n---\n\n"
                f"Rate: ${profile.min_hourly_rate}/hr\n"
                f"The proposal form is filled in the browser.\n\n"
                f"Say 'submit' to click Submit, or 'edit' to change something."
            )

        except Exception as exc:
            logger.warning("Browser apply failed: %s", exc)
            return (
                f"Browser couldn't complete the application.\n"
                f"Cover letter:\n---\n{letter}\n---\n\n"
                f"Apply manually: {job_url}"
            )

    async def _generate_letter(
        self, user_id: str, job: dict, profile, custom_note: str,
    ) -> str:
        """Generate cover letter with LazyClaw branding."""
        desc = job.get("description", "N/A")[:300]

        # Build prompt with optional LazyClaw branding
        branding = _LAZYCLAW_BRANDING if profile.branding_mode == "lazyclaw" else ""

        letter_prompt = (
            f"{branding}"
            f"Write a personalized freelance cover letter.\n\n"
            f"Job: {job.get('title', 'N/A')}\n"
            f"Description: {desc}\n"
            f"Budget: {job.get('budget', 'N/A')}\n\n"
            f"Profile:\n"
            f"Title: {profile.title}\n"
            f"Skills: {', '.join(profile.skills)}\n"
            f"Bio: {profile.bio}\n"
            f"{f'Note: {custom_note}' if custom_note else ''}\n\n"
            f"Requirements:\n"
            f"- Max 150 words\n"
            f"- Professional but not generic\n"
            f"- Reference specific parts of the job description\n"
            f"- Highlight relevant experience\n"
            f"- End with a clear call to action\n"
        )

        if profile.branding_mode == "lazyclaw":
            letter_prompt += (
                "- Mention that LazyClaw is an AI agent with human oversight\n"
                "- Frame AI as a strength (speed, availability, consistency)\n"
            )
        else:
            letter_prompt += (
                "- NO 'Dear Hiring Manager' or 'I am writing to express interest'\n"
                "- Sound human, not AI-generated\n"
            )

        # Try Claude Code MCP for letter generation
        registry = self._registry
        if registry is not None:
            for tool_info in registry.list_mcp_tools():
                func = tool_info.get("function", {})
                tname = func.get("name", "").lower()
                if "claude" in tname and "code" in tname:
                    tool = registry.get(func.get("name", ""))
                    if tool is not None:
                        try:
                            return await tool.execute(user_id, {"prompt": letter_prompt})
                        except Exception as exc:
                            logger.warning("Claude Code letter gen failed: %s", exc)

        # Fallback: template-based letter
        skills_str = ", ".join(profile.skills[:3]) if profile.skills else "various technologies"
        if profile.branding_mode == "lazyclaw":
            return (
                f"Hi,\n\n"
                f"I'm LazyClaw — an AI-powered development agent with human oversight. "
                f"I specialize in {skills_str} and can deliver your project "
                f"'{job.get('title', '')}' with 24/7 availability and automated testing.\n\n"
                f"Every deliverable is reviewed by my human founder before submission, "
                f"ensuring professional quality with AI speed.\n\n"
                f"Let's discuss the details — I can start immediately.\n\n"
                f"LazyClaw"
            )
        return (
            f"Hi,\n\n"
            f"I'm a {profile.title or 'developer'} with expertise in {skills_str}. "
            f"Your project '{job.get('title', '')}' aligns well with my experience.\n\n"
            f"{profile.bio[:200] if profile.bio else 'I deliver quality work on time.'}\n\n"
            f"Let's discuss the details.\n\n"
            f"Best regards"
        )
