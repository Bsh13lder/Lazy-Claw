"""Apply to a freelance job with LazyClaw-branded cover letter."""

from __future__ import annotations

import json
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_SEARCH_PREFIX = "SURVIVAL_SEARCH:"

# Letter-generation provenance markers returned by `_generate_letter`.
# Callers branch on these to decide whether the result reads as a genuine
# (LLM-written) draft or a degraded fallback that must be flagged.
_LETTER_SOURCE_LLM = "llm"            # EcoRouter / SDK / subscription path
_LETTER_SOURCE_TEMPLATE = "template"  # deterministic deep fallback
_LETTER_SOURCE_FAILED = "failed"      # no letter could be produced at all

# LazyClaw branding prompt injected into cover letter generation.
# Style anchor: warm + transparent + structured. Detailed style template
# in draft_proposal_skill.py:_PROMPT_TEMPLATE — keep these two in sync.
# Identity tokens ({display_name}, {github_url}, {value_pitch}) are filled
# from SkillsProfile at render time so a fresh user with their own name +
# pitch never gets a proposal signed by someone else.
_LAZYCLAW_BRANDING_TEMPLATE = (
    "IMPORTANT CONTEXT: You are writing this proposal on behalf of {display_name}, "
    "founder of LazyClaw — an open-source AI agent platform "
    "(MIT, Python, E2E encrypted). Use this exact style:\n\n"
    "OPENER ANCHOR — adapt this pitch to the specific job, do NOT replace it:\n"
    "  {value_pitch}\n\n"
    "STRUCTURE (in order):\n"
    "1. Warm opener — name + the pitch above (lightly adapted) + why this job fits (2-3 lines)\n"
    "2. 'How we'd work' section with these 3 transparency bullets:\n"
    "     ⚡ Fast responses (agent 24/7, founder approves in Madrid hours)\n"
    "     ✅ Supervised quality — every deliverable passes human review\n"
    "     💰 Competitive pricing — agent speed = savings passed on\n"
    "   AND state: day-to-day responder is the AI agent, founder reviews\n"
    "   EVERY deliverable, agent escalates to founder when human input needed.\n"
    "3. 'For your project I propose:' — numbered phase list (1..5) tailored\n"
    "   to THIS specific job. Each phase = one short concrete deliverable.\n"
    "4. 'About me' — if a GitHub URL is provided ({github_url}) include it\n"
    "   verbatim; otherwise skip the link. Add a stack relevance line.\n"
    "5. Next step — propose a 15-min discovery call. NEVER quote a fixed\n"
    "   price in the proposal itself.\n"
    "6. Sign-off: '— {display_name}'\n\n"
    "RULES:\n"
    "- 250-400 words (substantial, NOT 150)\n"
    "- Bullet / numbered lists ENCOURAGED for readability\n"
    "- Bold **key phrases** in markdown — Upwork renders it\n"
    "- Sparing emojis ONLY in the 3 transparency bullets (⚡ ✅ 💰)\n"
    "- If brief is Spanish, write ENTIRE proposal in Iberian Spanish\n"
    "  (tú, 'matrícula', 'presupuesto'). Otherwise English.\n"
    "- NO 'I hope this message finds you well' / 'Dear Hiring Manager'\n"
    "- NEVER promise to auto-submit anything to the client\n\n"
)


def _render_branding(profile) -> str:
    """Format the branding template with the user's identity fields."""
    return _LAZYCLAW_BRANDING_TEMPLATE.format(
        display_name=profile.display_name or "the founder",
        value_pitch=profile.value_pitch or (
            "(no pitch set — write a generic 1-sentence opener referencing "
            "the user's skills and LazyClaw)"
        ),
        github_url=profile.github_url or "(none set)",
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
        from lazyclaw.survival.gig import list_gigs, update_gig_status
        from lazyclaw.survival.platforms import BROWSER_PLATFORMS
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)
        ref = (params.get("job_reference", "") or "").strip()
        custom_note = params.get("custom_note", "")

        if not ref:
            return "Tell me which job to apply to (number, title, or URL)."

        # Resolve job via memory first, then fall back to direct Upwork MCP
        # lookup. Two-step fallback lets the brain succeed whether it used the
        # native `search_jobs` skill (writes SURVIVAL_SEARCH memory) or the
        # raw `upwork_search_jobs` MCP tool (does not).
        job = await self._resolve_from_memory(user_id, ref)
        if job is None:
            job = await self._resolve_via_upwork_mcp(user_id, ref)

        if job is None:
            return (
                f"Couldn't resolve job '{ref}'. "
                "Give me the full Upwork URL, or run 'search jobs' first."
            )

        # Generate cover letter with LazyClaw branding.
        letter, letter_source = await self._generate_letter(
            user_id, job, profile, custom_note,
        )

        # Truthful failure: when no letter could be produced at all (every
        # real path AND the template raised) we must NOT report progress.
        # The caller/brain would otherwise mark the apply "done" with nothing
        # to show. Surface a clear FAILURE so the UI says "couldn't apply".
        if letter_source == _LETTER_SOURCE_FAILED or not (letter or "").strip():
            return (
                f"FAILED to apply to **{job.get('title', ref)}**: "
                f"cover-letter generation failed (no letter produced). "
                f"No proposal was submitted. "
                f"Check the LLM route (MODE/transport) and try again."
            )

        # Only mark the gig 'applied' once we actually have a usable letter.
        # NOTE: this skill DRAFTS only — it never submits the proposal
        # (Upwork submission goes through `upwork_submit_proposal` or the
        # browser 'submit' step, both initiated separately). The gig status
        # 'applied' here means "draft prepared", not "proposal sent".
        found_gigs = await list_gigs(self._config, user_id, status="found")
        for gig in found_gigs:
            if gig.title == job.get("title") or gig.url == job.get("url"):
                await update_gig_status(
                    self._config, user_id, gig.id, "applied",
                    proposal_text=letter,
                )
                break

        # When only the deterministic template survived, the draft is
        # degraded (LLM unavailable). Flag it so the brain/user knows the
        # letter wasn't tailored by the model — never silently pass it off
        # as a finished, model-written proposal.
        degraded_note = (
            "\n\n⚠️ Note: the LLM was unavailable, so this is a generic "
            "TEMPLATE draft (not tailored to the brief). Review before sending."
            if letter_source == _LETTER_SOURCE_TEMPLATE else ""
        )

        # Browser platforms: fill the proposal form directly (no Submit click).
        if job.get("platform", "").lower() in BROWSER_PLATFORMS:
            return await self._apply_via_browser(
                user_id, job, profile, letter, degraded_note,
            )

        # Non-browser platforms: show letter + URL. Wording makes clear the
        # proposal has NOT been submitted — the user must do that manually.
        return (
            f"Cover letter DRAFTED (not yet submitted) for: "
            f"**{job['title']}** ({job.get('platform', 'N/A')})\n"
            f"Budget: {job.get('budget', 'N/A')}\n\n"
            f"---\n{letter}\n---\n\n"
            f"URL: {job.get('url', 'N/A')}\n\n"
            f"Open the link and submit manually, or say 'submit' to apply "
            f"via browser. Nothing has been sent yet.{degraded_note}"
        )

    async def _resolve_from_memory(self, user_id: str, ref: str) -> dict | None:
        """Look up the job in the latest SURVIVAL_SEARCH memory blob.

        Returns the matched job dict (number index or title substring) or
        None if no memory exists or no entry matches.
        """
        from lazyclaw.memory.personal import search_memories

        memories = await search_memories(
            self._config, user_id, _SEARCH_PREFIX, limit=5,
        )
        if not memories:
            return None

        content = memories[0]["content"]
        try:
            jobs = json.loads(content[len(_SEARCH_PREFIX):])
        except json.JSONDecodeError:
            return None

        if ref.isdigit():
            idx = int(ref) - 1
            if 0 <= idx < len(jobs):
                return jobs[idx]
            return None

        ref_lower = ref.lower()
        for j in jobs:
            if ref_lower in j.get("title", "").lower():
                return j
        return None

    async def _resolve_via_upwork_mcp(
        self, user_id: str, ref: str,
    ) -> dict | None:
        """Resolve a job directly through mcp-upwork when memory is empty.

        Handles the common case where the brain searched via the raw
        `upwork_search_jobs` MCP tool (which doesn't write SURVIVAL_SEARCH
        memory) and then asked to apply by title or URL.

        - URL → upwork_get_job_details
        - title → upwork_search_jobs, pick best title-substring match
        """
        registry = self._registry
        if registry is None:
            return None

        details_tool = None
        search_tool = None
        for tool_info in registry.list_mcp_tools():
            tname = tool_info.get("function", {}).get("name", "")
            tail = tname.split("_")[-1] if tname else ""
            # Match by suffix so name-prefix shape (mcp_<id>_<tool>) is irrelevant.
            if tname.endswith("upwork_get_job_details"):
                details_tool = registry.get(tname)
            elif tname.endswith("upwork_search_jobs"):
                search_tool = registry.get(tname)
            if details_tool and search_tool:
                break

        is_url = ref.startswith("http://") or ref.startswith("https://")

        if is_url and details_tool is not None:
            try:
                raw = await details_tool.execute(user_id, {"job_url": ref})
            except Exception as exc:
                logger.warning("upwork_get_job_details failed: %s", exc)
                return None
            return self._coerce_to_job_dict(raw, fallback_url=ref)

        if search_tool is not None:
            try:
                raw = await search_tool.execute(
                    user_id, {"query": ref, "limit": 5},
                )
            except Exception as exc:
                logger.warning("upwork_search_jobs failed: %s", exc)
                return None

            jobs = self._coerce_to_job_list(raw)
            if not jobs:
                return None

            ref_lower = ref.lower()
            for j in jobs:
                if ref_lower in (j.get("title") or "").lower():
                    return self._normalize_mcp_job(j)
            return self._normalize_mcp_job(jobs[0])

        return None

    @staticmethod
    def _coerce_to_job_list(raw) -> list[dict]:
        """Best-effort unwrap of MCP tool return into list[dict]."""
        if raw is None:
            return []
        if isinstance(raw, list):
            return [j for j in raw if isinstance(j, dict)]
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return []
        else:
            data = raw
        if isinstance(data, dict):
            inner = (
                data.get("jobs")
                or data.get("results")
                or data.get("items")
                or []
            )
            return [j for j in inner if isinstance(j, dict)]
        if isinstance(data, list):
            return [j for j in data if isinstance(j, dict)]
        return []

    @classmethod
    def _coerce_to_job_dict(cls, raw, fallback_url: str = "") -> dict | None:
        """Wrap upwork_get_job_details into apply_job's expected job shape."""
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None
        if not isinstance(raw, dict):
            return None
        job = cls._normalize_mcp_job(raw)
        if fallback_url and not job.get("url"):
            job["url"] = fallback_url
        return job

    @staticmethod
    def _normalize_mcp_job(j: dict) -> dict:
        """Map an upwork-mcp job dict onto the keys apply_job consumes."""
        url = j.get("url") or j.get("link") or ""
        if url and not url.startswith("http"):
            # Upstream uses two shapes: absolute path "/jobs/~id" or bare id.
            if url.startswith("/"):
                url = f"https://www.upwork.com{url}"
            else:
                url = f"https://www.upwork.com/jobs/{url}"
        return {
            "title": j.get("title") or j.get("name") or "Untitled",
            "description": j.get("description") or j.get("snippet") or "",
            "budget": j.get("budget") or j.get("hourly_rate") or "N/A",
            "url": url,
            "platform": "upwork",
        }

    async def _apply_via_browser(
        self, user_id: str, job: dict, profile, letter: str,
        degraded_note: str = "",
    ) -> str:
        """Fill Upwork proposal form via browser. Does NOT click Submit."""
        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            return (
                f"Cover letter DRAFTED (not submitted) but browser not "
                f"available.\n\n"
                f"---\n{letter}\n---\n\n"
                f"Apply manually at: {job.get('url', 'N/A')}{degraded_note}"
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
                f"Proposal form FILLED (NOT yet submitted) on Upwork for: "
                f"**{job['title']}**\n\n"
                f"---\n{letter}\n---\n\n"
                f"Rate: ${profile.min_hourly_rate}/hr\n"
                f"The proposal form is filled in the browser but the Submit "
                f"button has NOT been clicked.\n\n"
                f"Say 'submit' to click Submit, or 'edit' to change "
                f"something.{degraded_note}"
            )

        except Exception as exc:
            logger.warning("Browser apply failed: %s", exc)
            return (
                f"Browser couldn't complete the application — proposal NOT "
                f"submitted.\n"
                f"Cover letter draft:\n---\n{letter}\n---\n\n"
                f"Apply manually: {job_url}{degraded_note}"
            )

    async def _generate_letter(
        self, user_id: str, job: dict, profile, custom_note: str,
    ) -> tuple[str, str]:
        """Generate cover letter with LazyClaw branding.

        Returns ``(letter, source)`` where ``source`` is one of
        ``_LETTER_SOURCE_LLM`` (model-written via EcoRouter / subscription),
        ``_LETTER_SOURCE_TEMPLATE`` (deterministic deep fallback), or
        ``_LETTER_SOURCE_FAILED`` (nothing could be produced). The caller
        uses ``source`` to keep the result truthful: an LLM draft reads as a
        normal draft, a template draft is flagged as degraded, and a failure
        is reported as a failure (never as a finished proposal).
        """
        # 1500 chars of brief — enough room for the LLM to tailor the phase
        # list to the actual problem rather than guessing.
        desc = job.get("description", "N/A")[:1500]

        # Build prompt with optional LazyClaw branding
        branding = _render_branding(profile) if profile.branding_mode == "lazyclaw" else ""

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
        )

        if profile.branding_mode == "lazyclaw":
            # Branding template above already specifies length, structure,
            # tone — don't re-state requirements here, it confuses the model.
            letter_prompt += (
                "Reference SPECIFIC parts of the job description in the\n"
                "phase list — show you read the brief.\n"
            )
        else:
            letter_prompt += (
                "Requirements:\n"
                "- Max 150 words\n"
                "- Professional but not generic\n"
                "- Reference specific parts of the job description\n"
                "- Highlight relevant experience\n"
                "- End with a clear call to action\n"
                "- NO 'Dear Hiring Manager' or 'I am writing to express interest'\n"
                "- Sound human, not AI-generated\n"
            )

        # Letter-generation ladder (revised 2026-06-02):
        #   1. EcoRouter (PRIMARY) — respects the user's active mode. Under
        #      MODE_CLAUDE this dispatches via the claude-agent-sdk on the
        #      Max subscription ($0, working). Under HYBRID it uses the
        #      configured brain. This is the ONLY model-backed path.
        #   2. Template (deep fallback) — deterministic, used only when the
        #      EcoRouter path fails or returns empty.
        # The Claude Code MCP / raw `claude -p` letter-gen step was REMOVED:
        # its subprocess picks up the (dead, $0-balance) ANTHROPIC_API_KEY
        # from the container env and fails with "Credit balance is too low",
        # which used to run BEFORE the working subscription path. EcoRouter
        # already knows about the subscription transport — go straight to it.
        try:
            from lazyclaw.llm.providers.base import LLMMessage
            from lazyclaw.llm.router import LLMRouter
            from lazyclaw.llm.eco_router import EcoRouter, ROLE_BRAIN
            eco_router = EcoRouter(self._config, LLMRouter(self._config))
            resp = await eco_router.chat(
                messages=[LLMMessage(role="user", content=letter_prompt)],
                user_id=user_id,
                role=ROLE_BRAIN,
                max_tokens=1100,
                temperature=0.7,
            )
            text = (resp.content or "").strip()
            if text:
                return text, _LETTER_SOURCE_LLM
            logger.warning("EcoRouter returned empty letter, falling to template")
        except Exception as exc:
            logger.warning("EcoRouter letter gen failed: %s", exc)

        # Deep fallback: template-based letter (used when the LLM path is
        # unavailable or empty). Wrapped so a failure here is reported as a
        # genuine failure rather than masquerading as a finished proposal.
        try:
            return self._template_letter(job, profile), _LETTER_SOURCE_TEMPLATE
        except Exception as exc:
            logger.warning("Template letter gen failed: %s", exc)
            return "", _LETTER_SOURCE_FAILED

    def _template_letter(self, job: dict, profile) -> str:
        """Deterministic deep-fallback cover letter (no LLM).

        Keep aligned with the structure in _LAZYCLAW_BRANDING_TEMPLATE.
        """
        skills_str = ", ".join(profile.skills[:3]) if profile.skills else "various technologies"
        signoff = profile.display_name or "the founder"
        github_block = (
            f"**About me:** open-source code at {profile.github_url}\n\n"
            if profile.github_url else ""
        )
        # Pitch opener — falls back to a skills-based 1-liner so the letter
        # still flows when the user hasn't set a value_pitch yet.
        pitch_opener = (
            profile.value_pitch
            or (
                f"My name is {signoff} and I run **LazyClaw** — an open-source "
                f"AI agent platform (MIT, Python, E2E encrypted)."
            )
        )
        if profile.branding_mode == "lazyclaw":
            return (
                f"Hi,\n\n"
                f"{pitch_opener} Your project '{job.get('title', '')}' fits "
                f"my stack ({skills_str}) directly.\n\n"
                f"**How we'd work:** day-to-day responses come from my "
                f"personal AI agent (LazyClaw); the agent does the technical "
                f"work, but **I review and approve every deliverable** before "
                f"it reaches you — nothing ships without human sign-off.\n\n"
                f"⚡ Fast responses (agent 24/7, I approve in Madrid hours)\n"
                f"✅ Supervised quality — human review on every delivery\n"
                f"💰 Competitive pricing — agent speed = savings passed on\n\n"
                f"{github_block}"
                f"**Next step:** a 15-min discovery call to scope the work, "
                f"then I'll send a phased plan with fixed prices per "
                f"deliverable — no surprises.\n\n"
                f"— {signoff}"
            )
        return (
            f"Hi,\n\n"
            f"I'm a {profile.title or 'developer'} with expertise in {skills_str}. "
            f"Your project '{job.get('title', '')}' aligns well with my experience.\n\n"
            f"{profile.bio[:200] if profile.bio else 'I deliver quality work on time.'}\n\n"
            f"Let's discuss the details.\n\n"
            f"Best regards"
        )
