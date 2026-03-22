"""Survival instinct skills: job hunting, proposals, and tracking.

Six skills:
  - set_skills_profile: configure freelance profile
  - search_jobs: find matching jobs via JobSpy MCP or browser
  - apply_job: generate cover letter + submit (user must approve)
  - survival_mode: toggle automatic job hunting cron
  - survival_status: show stats (no LLM, instant)
  - review_deliverable: quality gate before submitting work to client
"""

from __future__ import annotations

import json
import logging
import re

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Memory content prefixes for structured storage
_SEARCH_PREFIX = "SURVIVAL_SEARCH:"
_APP_PREFIX = "SURVIVAL_APP:"


# ── SetSkillsProfileSkill ─────────────────────────────────────────────


class SetSkillsProfileSkill(BaseSkill):
    """Set or view the user's freelance skills profile."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_skills_profile"

    @property
    def description(self) -> str:
        return (
            "Set your freelance skills profile for job matching. "
            "Platforms available: upwork, indeed, glassdoor, freelancer, fiverr. "
            "Usage: 'my skills are python, fastapi, react' or "
            "'set minimum rate $40/hour' or 'set title Senior Python Developer'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Your professional skills",
                },
                "title": {
                    "type": "string",
                    "description": "Professional title",
                },
                "bio": {
                    "type": "string",
                    "description": "Short professional bio (2-3 sentences)",
                },
                "min_hourly_rate": {
                    "type": "number",
                    "description": "Minimum hourly rate in USD",
                },
                "min_fixed_rate": {
                    "type": "number",
                    "description": "Minimum fixed price in USD",
                },
                "platforms": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["upwork", "indeed", "glassdoor", "freelancer", "fiverr"],
                    },
                    "description": "Platforms to hunt on: upwork, indeed, glassdoor, freelancer, fiverr",
                },
                "excluded_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to exclude from job results",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.profile import _coerce_updates, get_profile, update_profile

        raw_updates = {
            k: v
            for k, v in params.items()
            if v is not None and v != "" and v != []
        }

        if not raw_updates:
            profile = await get_profile(self._config, user_id)
            if not profile.skills:
                return "No skills profile set. Tell me your skills, title, and rates."
            return (
                f"Your Skills Profile:\n\n"
                f"Title: {profile.title or 'Not set'}\n"
                f"Skills: {', '.join(profile.skills) or 'None'}\n"
                f"Bio: {profile.bio or 'Not set'}\n"
                f"Min hourly: ${profile.min_hourly_rate}/hr\n"
                f"Min fixed: ${profile.min_fixed_rate}\n"
                f"Platforms: {', '.join(profile.platforms)}\n"
                f"Excluded: {', '.join(profile.excluded_keywords) or 'None'}\n"
                f"Max concurrent: {profile.max_concurrent_jobs}"
            )

        # Validate and coerce values before saving
        updates = _coerce_updates(raw_updates)
        if isinstance(updates, str):
            return updates  # validation error message

        await update_profile(self._config, user_id, updates)
        changed = ", ".join(f"{k}={v}" for k, v in updates.items())
        return f"Profile updated: {changed}"


# ── SearchJobsSkill ───────────────────────────────────────────────────


class SearchJobsSkill(BaseSkill):
    """Search for freelance jobs matching user's skills profile."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "search_jobs"

    @property
    def description(self) -> str:
        return (
            "Search for freelance jobs matching your skills profile. "
            "Searches Indeed, Glassdoor, Upwork, ZipRecruiter. "
            "Returns ranked matches with relevance scores. "
            "Usage: 'find me jobs' or 'search python freelance jobs'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": "Search keywords (uses skills profile if empty)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results per platform (default: 10)",
                    "default": 10,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.memory.personal import save_memory
        from lazyclaw.survival.matcher import score_job
        from lazyclaw.survival.platforms import BROWSER_PLATFORMS, JOBSPY_PLATFORMS, PLATFORMS
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)
        keywords = params.get("keywords", "") or " ".join(profile.skills[:5])
        try:
            max_results = min(int(params.get("max_results", 10)), 50)
        except (ValueError, TypeError):
            max_results = 10

        if not keywords:
            return (
                "No search keywords and no skills profile set. "
                "Set your profile first: 'my skills are python, fastapi, react'"
            )

        all_jobs: list[dict] = []

        # Split user's platforms into MCP-capable and browser-only
        mcp_platforms = [p for p in profile.platforms if p in JOBSPY_PLATFORMS]
        browser_platforms = [p for p in profile.platforms if p in BROWSER_PLATFORMS]

        # 1. JobSpy MCP path — Indeed, Glassdoor, etc. (fast)
        if mcp_platforms:
            mcp_result = await self._try_jobspy(user_id, keywords, max_results)
            if mcp_result is not None:
                if isinstance(mcp_result, str):
                    try:
                        parsed = json.loads(mcp_result)
                        mcp_jobs = (
                            parsed.get("jobs", parsed)
                            if isinstance(parsed, dict)
                            else parsed
                        )
                    except json.JSONDecodeError:
                        mcp_jobs = []
                else:
                    mcp_jobs = (
                        mcp_result if isinstance(mcp_result, list) else []
                    )
                all_jobs.extend(mcp_jobs)

        # 2. Browser path — Upwork, etc. (slower, needs login)
        for platform_name in browser_platforms:
            platform = PLATFORMS.get(platform_name)
            if not platform:
                continue
            browser_jobs = await self._search_via_browser(
                user_id, keywords, platform
            )
            all_jobs.extend(browser_jobs)

        # 3. Fallback: if no platforms configured or nothing found, try web search
        if not all_jobs and not mcp_platforms and not browser_platforms:
            return await self._search_via_web(user_id, keywords)

        if not all_jobs:
            platforms_str = ", ".join(profile.platforms) or "configured platforms"
            return f"No jobs found for '{keywords}' on {platforms_str}."

        # Score and rank — lower threshold for browser results (less structured)
        scored = [score_job(j, profile) for j in all_jobs]
        scored.sort(key=lambda j: j.match_score, reverse=True)
        threshold = 0.3 if browser_platforms else 0.7
        good = [j for j in scored if j.match_score >= threshold]

        if not good:
            return (
                f"Found {len(all_jobs)} jobs but none matched your profile well enough. "
                "Try broader keywords or lower your minimum rate."
            )

        # Format response (max 5)
        lines = [f"Found {len(good)} matching jobs:\n"]
        for i, job in enumerate(good[:5], 1):
            pct = int(job.match_score * 100)
            lines.append(f"{i}. **{job.title}** — {job.budget}")
            lines.append(f"   Platform: {job.platform} | Match: {pct}%")
            if job.match_reasons:
                lines.append(f"   {', '.join(job.match_reasons[:3])}")
            if job.url:
                lines.append(f"   {job.url}")
            lines.append("")

        lines.append("Reply with a number to apply, or 'skip all'.")

        # Store only displayed jobs so apply_job indices match
        search_data = json.dumps([
            {
                "job_id": j.job_id,
                "title": j.title,
                "platform": j.platform,
                "budget": j.budget,
                "url": j.url,
                "score": j.match_score,
                "description": j.description[:200],
            }
            for j in good[:5]
        ])
        await save_memory(
            self._config,
            user_id,
            f"{_SEARCH_PREFIX}{search_data}",
            memory_type="survival",
            importance=3,
        )

        return "\n".join(lines)

    async def _try_jobspy(
        self, user_id: str, keywords: str, max_results: int
    ) -> list | dict | str | None:
        """Try to call JobSpy MCP. Returns None if not available."""
        registry = self._registry
        if registry is None:
            return None

        # Look for jobspy tool under various names
        for tool_name in ("mcp_jobspy_search_jobs", "mcp_mcp-jobspy_search_jobs", "jobspy_search_jobs"):
            tool = registry.get(tool_name)
            if tool is not None:
                try:
                    return await tool.execute(user_id, {
                        "search_term": keywords,
                        "results_wanted": max_results,
                        "hours_old": 72,
                    })
                except Exception as exc:
                    logger.warning("JobSpy MCP call failed: %s", exc)
                    return None

        return None

    async def _search_via_web(self, user_id: str, keywords: str) -> str:
        """Fallback: search via web_search skill."""
        registry = self._registry
        if registry is None:
            return "No job search tools available. Install JobSpy MCP: lazyclaw install-mcps"

        web_search = registry.get("web_search")
        if web_search is not None:
            try:
                results = await web_search.execute(user_id, {
                    "query": f"site:upwork.com {keywords} freelance job",
                })
                return (
                    f"JobSpy MCP not installed. Browser search results:\n\n{results}\n\n"
                    "Install JobSpy for better results: lazyclaw install-mcps"
                )
            except Exception as exc:
                logger.warning("Web search fallback failed: %s", exc)

        return "No job search tools available. Install JobSpy MCP: lazyclaw install-mcps"

    async def _search_via_browser(
        self, user_id: str, keywords: str, platform
    ) -> list[dict]:
        """Browse a platform website to find jobs. Returns list of job dicts."""
        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            logger.warning("Browser skill not available for %s search", platform.name)
            return []

        search_url = platform.base_url + platform.search_path.format(
            keywords=keywords.replace(" ", "%20")
        )

        try:
            # Open search page
            await browser.execute(user_id, {"action": "open", "url": search_url})

            # Read the page content (accessibility tree — semantic snapshot)
            page_text = await browser.execute(user_id, {"action": "read"})

            if not page_text or len(page_text) < 100:
                logger.warning("Empty page from %s — may need login", platform.name)
                return []

            # Parse jobs from accessibility tree text
            if platform.name == "Upwork":
                return _parse_upwork_jobs(page_text)
            return _parse_generic_jobs(page_text, platform.name)

        except Exception as exc:
            logger.warning("Browser search on %s failed: %s", platform.name, exc)
            return []


def _parse_upwork_jobs(page_text: str) -> list[dict]:
    """Best-effort parse of Upwork search results from accessibility tree.

    Upwork job cards in the accessibility tree have links to /jobs/~<id>.
    We split on those boundaries and extract title, budget, and skills.
    """
    jobs: list[dict] = []

    # Split by job-link boundaries
    sections = re.split(r'(?=link\s+"[^"]*"\s+url="/jobs/~)', page_text)

    for section in sections:
        if "/jobs/~" not in section:
            continue

        # Extract job URL
        url_match = re.search(r'url="(/jobs/~\w+)"', section)
        if not url_match:
            continue
        job_path = url_match.group(1)
        job_id = job_path.split("~")[-1] if "~" in job_path else job_path

        # Extract title (usually in heading or the link text itself)
        title_match = re.search(r'(?:heading[^"]*"|link\s+")(.*?)"', section)
        title = title_match.group(1) if title_match else "Untitled"

        # Extract budget
        budget = "N/A"
        budget_match = re.search(
            r"\$[\d,.]+(?:\s*[-\u2013]\s*\$[\d,.]+)?(?:/hr)?", section
        )
        if budget_match:
            budget = budget_match.group(0)
        est_match = re.search(
            r"Est\.?\s*budget:?\s*\$[\d,.]+", section, re.IGNORECASE
        )
        if est_match:
            budget = est_match.group(0)

        # Extract description snippet
        desc = ""
        desc_match = re.search(r'text\s+"([^"]{50,})"', section)
        if desc_match:
            desc = desc_match.group(1)[:500]

        # Extract skills tags — filter out non-skill text
        raw_skills = re.findall(r'text\s+"([A-Za-z][A-Za-z0-9+#. ]{1,30})"', section)
        skills = [
            s.strip()
            for s in raw_skills
            if not re.match(r"^\$|^Posted|^Est|^Hourly|^Fixed", s)
        ]

        jobs.append({
            "id": job_id,
            "title": title,
            "company": "",  # Upwork hides client name in search
            "location": "Remote",
            "site": "upwork",
            "platform": "upwork",
            "url": f"https://www.upwork.com{job_path}",
            "description": desc,
            "budget": budget,
            "salary": budget,
            "skills": skills[:10],
        })

    return jobs


def _parse_generic_jobs(page_text: str, platform_name: str) -> list[dict]:
    """Fallback parser — extract what we can from any platform's accessibility tree."""
    jobs: list[dict] = []
    links = re.findall(r'link\s+"([^"]+)"\s+url="([^"]+)"', page_text)

    _SKIP_WORDS = frozenset({
        "sign in", "log in", "home", "menu", "search", "filter",
        "about", "help", "contact", "privacy", "terms",
    })

    for title, url in links:
        if len(title) < 10 or len(title) > 200:
            continue
        title_lower = title.lower()
        if any(skip in title_lower for skip in _SKIP_WORDS):
            continue
        jobs.append({
            "id": url.split("/")[-1],
            "title": title,
            "company": "",
            "location": "",
            "site": platform_name.lower(),
            "platform": platform_name.lower(),
            "url": url if url.startswith("http") else "",
            "description": "",
            "budget": "N/A",
            "salary": "",
        })

    return jobs[:20]  # cap at 20


# ── ApplyJobSkill ─────────────────────────────────────────────────────


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
            "The letter is generated by AI based on your profile and the job. "
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
        from lazyclaw.survival.platforms import BROWSER_PLATFORMS
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)
        ref = params.get("job_reference", "")
        custom_note = params.get("custom_note", "")

        # Find last search results from memory
        memories = await search_memories(
            self._config, user_id, _SEARCH_PREFIX, limit=5
        )

        if not memories:
            return "No recent job search. Use 'search jobs' first."

        # Use the most recent search results
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

        # Generate cover letter
        letter = await self._generate_letter(user_id, job, profile, custom_note)

        # Browser platforms: fill the proposal form directly
        if job.get("platform", "").lower() in BROWSER_PLATFORMS:
            return await self._apply_via_browser(user_id, job, profile, letter)

        # Non-browser platforms: show letter + URL for manual submission
        return (
            f"Cover letter for: **{job['title']}** ({job['platform']})\n"
            f"Budget: {job['budget']}\n\n"
            f"---\n{letter}\n---\n\n"
            f"Platform: {job['platform']}\n"
            f"URL: {job.get('url', 'N/A')}\n\n"
            f"Open the link and submit manually, or say 'submit' to apply via browser."
        )

    async def _apply_via_browser(
        self, user_id: str, job: dict, profile, letter: str
    ) -> str:
        """Submit a proposal on Upwork via browser.

        Fills the form but does NOT click Submit — returns a preview
        for user approval. The agent clicks Submit only after user confirms
        (permission_hint="ask" gates this).
        """
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
            # Step 1: Open the job posting
            await browser.execute(user_id, {"action": "open", "url": job_url})

            # Step 2: Read to confirm we're on the right page
            await browser.execute(user_id, {"action": "read"})

            # Step 3: Click "Submit a Proposal" or "Apply Now"
            await browser.execute(user_id, {
                "action": "click",
                "target": "Submit a Proposal",
            })

            # Step 4: Read the proposal form
            await browser.execute(user_id, {"action": "read"})

            # Step 5: Fill in the cover letter
            await browser.execute(user_id, {
                "action": "type",
                "target": "Cover Letter",
                "text": letter,
            })

            # Step 6: Set the rate if hourly
            if profile.min_hourly_rate > 0:
                await browser.execute(user_id, {
                    "action": "type",
                    "target": "Hourly Rate",
                    "text": str(profile.min_hourly_rate),
                })

            # DON'T submit — return preview for user approval
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
        self,
        user_id: str,
        job: dict,
        profile,
        custom_note: str,
    ) -> str:
        """Generate a cover letter via Claude Code MCP or fallback."""
        # Truncate external description to limit prompt injection surface
        desc = job.get("description", "N/A")[:300]
        letter_prompt = (
            f"Write a personalized Upwork/freelance cover letter.\n\n"
            f"Job: {job['title']}\n"
            f"Description: {desc}\n"
            f"Budget: {job['budget']}\n\n"
            f"My profile:\n"
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
            f"- NO 'Dear Hiring Manager' or 'I am writing to express interest'\n"
            f"- Sound human, not AI-generated"
        )

        # Try Claude Code MCP
        registry = self._registry
        if registry is not None:
            for tool_name in ("mcp_claude_code", "claude_code"):
                tool = registry.get(tool_name)
                if tool is not None:
                    try:
                        return await tool.execute(user_id, {"prompt": letter_prompt})
                    except Exception as exc:
                        logger.warning("Claude Code MCP letter gen failed: %s", exc)

        # Fallback: template-based letter
        skills_str = ", ".join(profile.skills[:3]) if profile.skills else "various technologies"
        return (
            f"Hi,\n\n"
            f"I'm a {profile.title or 'developer'} with expertise in {skills_str}. "
            f"Your project '{job['title']}' aligns well with my experience.\n\n"
            f"{profile.bio[:200] if profile.bio else 'I deliver quality work on time.'}\n\n"
            f"Let's discuss the details.\n\n"
            f"Best regards"
        )


# ── SurvivalModeSkill ─────────────────────────────────────────────────


class SurvivalModeSkill(BaseSkill):
    """Enable or disable automatic job hunting."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "survival_mode"

    @property
    def description(self) -> str:
        return (
            "Enable or disable survival mode. When ON, the agent automatically "
            "searches for matching jobs every 30 minutes and notifies you on Telegram. "
            "Usage: 'enable survival mode' or 'turn off job hunting'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true to enable, false to disable",
                },
            },
            "required": ["enabled"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import create_job
        from lazyclaw.survival.profile import get_profile

        enabled = params.get("enabled", False)
        profile = await get_profile(self._config, user_id)

        if enabled and not profile.skills:
            return (
                "Set up your skills profile first!\n\n"
                "Tell me:\n"
                "- Your skills (e.g., 'my skills are python, fastapi, react')\n"
                "- Your minimum rate (e.g., 'minimum $30/hour')\n"
                "- Which platforms (e.g., 'search on upwork')\n\n"
                "Then enable survival mode."
            )

        if enabled:
            # Remove existing survival crons first (idempotent enable)
            await self._remove_survival_jobs(user_id)

            keywords = " ".join(profile.skills[:5])

            # Job search cron (every 30 min)
            await create_job(
                self._config,
                user_id,
                name="survival_job_search",
                instruction=(
                    f"Search for freelance jobs matching my profile: {keywords}. "
                    "Only show 70%+ matches. Send results to Telegram."
                ),
                job_type="cron",
                cron_expression="*/30 * * * *",
                context=json.dumps({"survival_mode": True}),
            )

            # Message checker cron (every 15 min)
            await create_job(
                self._config,
                user_id,
                name="survival_message_check",
                instruction=(
                    "Check for new messages from clients on Upwork. "
                    "Notify me on Telegram if any need response."
                ),
                job_type="cron",
                cron_expression="*/15 * * * *",
                context=json.dumps({"survival_mode": True}),
            )

            return (
                f"Survival mode ON\n\n"
                f"Skills: {', '.join(profile.skills)}\n"
                f"Min rate: ${profile.min_hourly_rate}/hr\n"
                f"Platforms: {', '.join(profile.platforms)}\n"
                f"Max concurrent jobs: {profile.max_concurrent_jobs}\n\n"
                f"Checking for jobs every 30 min\n"
                f"Checking client messages every 15 min\n\n"
                f"I'll notify you on Telegram when I find matches."
            )

        # Disable
        removed = await self._remove_survival_jobs(user_id)
        if removed:
            return "Survival mode OFF. Job hunting paused."
        return "Survival mode was already OFF."

    async def _remove_survival_jobs(self, user_id: str) -> int:
        """Remove all survival cron jobs. Returns count removed."""
        from lazyclaw.heartbeat.orchestrator import delete_job, list_jobs

        jobs = await list_jobs(self._config, user_id)
        removed = 0
        for job in jobs:
            name = job.get("name", "")
            if name.startswith("survival_"):
                await delete_job(self._config, user_id, job["id"])
                removed += 1
        return removed


# ── SurvivalStatusSkill ───────────────────────────────────────────────


class SurvivalStatusSkill(BaseSkill):
    """Show survival mode status and stats. No LLM call — instant."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "survival_status"

    @property
    def description(self) -> str:
        return (
            "Show survival mode status: active jobs, pending applications, earnings. "
            "Usage: 'survival status' or 'how much did I earn'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import list_jobs
        from lazyclaw.memory.personal import search_memories
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)

        # Check if survival crons are active
        jobs = await list_jobs(self._config, user_id)
        survival_crons = [
            j
            for j in jobs
            if j.get("name", "").startswith("survival_")
            and j.get("status") == "active"
        ]
        is_active = len(survival_crons) > 0

        # Load application tracking from memory
        app_memories = await search_memories(
            self._config, user_id, _APP_PREFIX, limit=50
        )

        applications: list[dict] = []
        for mem in app_memories:
            content = mem["content"]
            json_str = content[len(_APP_PREFIX):]
            try:
                applications.append(json.loads(json_str))
            except json.JSONDecodeError:
                continue

        active_apps = [
            a for a in applications if a.get("status") in ("applied", "interviewing")
        ]
        working = [a for a in applications if a.get("status") == "working"]
        completed = [a for a in applications if a.get("status") == "completed"]
        total_earned = sum(a.get("amount", 0) for a in completed)

        status_icon = "ON" if is_active else "OFF"
        lines = [f"Survival Mode: {status_icon}\n"]

        if profile.skills:
            lines.append(f"Skills: {', '.join(profile.skills[:5])}")
            lines.append(f"Min rate: ${profile.min_hourly_rate}/hr")

        lines.append("\nStats:")
        lines.append(f"  Applications: {len(active_apps)} pending")
        lines.append(f"  Working on: {len(working)} jobs")
        lines.append(f"  Completed: {len(completed)} jobs")
        lines.append(f"  Earned: ${total_earned:.2f}")

        if active_apps:
            lines.append("\nPending Applications:")
            for a in active_apps[:5]:
                lines.append(
                    f"  - {a.get('title', '?')} ({a.get('platform', '?')}) "
                    f"— {a.get('budget', '?')}"
                )

        if working:
            lines.append("\nCurrently Working:")
            for w in working[:3]:
                lines.append(f"  - {w.get('title', '?')} — {w.get('budget', '?')}")

        return "\n".join(lines)


# ── ReviewDeliverableSkill ────────────────────────────────────────────

_CODE_KEYWORDS = frozenset({
    "api", "code", "script", "function", "bug", "fix", "build",
    "develop", "implement", "backend", "frontend", "endpoint",
    "database", "migration", "test", "deploy", "refactor",
    "software", "program", "app", "website", "server",
})


class ReviewDeliverableSkill(BaseSkill):
    """Review work before submitting to a client.

    Code tasks: Claude Code MCP reads files, runs tests, auto-fixes (max 3 rounds).
    Non-code tasks: LLM text review via gpt-5-mini.
    """

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "review_deliverable"

    @property
    def description(self) -> str:
        return (
            "Review work before submitting to a client. "
            "For code: reads files, runs tests, checks requirements. "
            "Auto-fixes issues (max 3 rounds). "
            "Usage: 'review my work' or 'check deliverable before submit'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "job_description": {
                    "type": "string",
                    "description": "The original job requirements/description",
                },
                "deliverable_summary": {
                    "type": "string",
                    "description": "What was built/written (summary or file paths)",
                },
                "auto_fix": {
                    "type": "boolean",
                    "description": "Auto-fix issues using Claude Code (default true)",
                    "default": True,
                },
            },
            "required": ["job_description"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        job_desc = params.get("job_description", "")
        deliverable = params.get("deliverable_summary", "")
        auto_fix = params.get("auto_fix", True)

        is_code = any(k in job_desc.lower() for k in _CODE_KEYWORDS)

        if is_code:
            return await self._review_code(
                user_id, job_desc, deliverable, auto_fix
            )
        return await self._review_text(user_id, job_desc, deliverable)

    async def _review_code(
        self,
        user_id: str,
        job_desc: str,
        deliverable: str,
        auto_fix: bool,
    ) -> str:
        """Use Claude Code MCP to review code deliverables."""
        claude_tool = self._find_claude_tool()
        if claude_tool is None:
            return await self._review_text(user_id, job_desc, deliverable)

        # Truncate external description to limit prompt injection surface
        desc = job_desc[:500]
        deliv = deliverable[:500] if deliverable else ""

        review_prompt = (
            "You are a strict code reviewer checking freelance deliverables.\n\n"
            f"Job requirements:\n{desc}\n\n"
            f"{'Deliverable info: ' + deliv if deliv else ''}\n\n"
            "Do the following:\n"
            "1. Read ALL files that were created or modified for this job\n"
            "2. Run any existing tests (pytest, npm test, go test, etc.)\n"
            "3. Check: does the code meet EVERY requirement?\n"
            "4. Check: any bugs, edge cases, missing error handling?\n"
            "5. Check: security issues (injection, auth bypass, data leaks)?\n"
            "6. Check: code quality — would a senior dev approve this?\n\n"
            "Score: PASS / NEEDS_WORK / FAIL\n"
            "If not PASS, list EXACT issues with file paths and line numbers.\n"
            "If tests fail, show the failure output."
        )

        review_text = await self._call_claude(claude_tool, user_id, review_prompt)

        if "PASS" in review_text.upper():
            return (
                f"Code Review: PASS\n\n{review_text}\n\n"
                "Ready to submit to client."
            )

        if not auto_fix:
            return (
                f"Code Review: NEEDS WORK\n\n{review_text}\n\n"
                "Fix the issues manually, then run review again."
            )

        # Auto-fix loop (max 3 rounds)
        for attempt in range(1, 4):
            fix_prompt = (
                f"Fix ALL issues found in this code review:\n\n"
                f"{review_text}\n\n"
                "After fixing, run all tests to verify."
            )
            await self._call_claude(claude_tool, user_id, fix_prompt)

            # Re-review
            review_text = await self._call_claude(
                claude_tool, user_id, review_prompt
            )

            if "PASS" in review_text.upper():
                rounds = "round" if attempt == 1 else "rounds"
                return (
                    f"Code Review: PASS (after {attempt} fix {rounds})\n\n"
                    f"{review_text}\n\n"
                    "Ready to submit to client."
                )

        return (
            f"Still has issues after 3 fix rounds:\n\n{review_text}\n\n"
            "Review manually before submitting."
        )

    async def _review_text(
        self, user_id: str, job_desc: str, deliverable: str
    ) -> str:
        """Text-based review for non-code deliverables.

        Uses EcoRouter which routes through free providers first
        (OpenRouter/Qwen, Groq, Ollama) before falling back to paid.
        """
        from lazyclaw.llm.eco_router import EcoRouter
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter

        desc = job_desc[:500]
        deliv = deliverable[:500] if deliverable else "(no deliverable summary provided)"

        review_prompt = (
            "You are a strict client reviewing freelance work.\n\n"
            f"Job requirements:\n{desc}\n\n"
            f"Deliverable:\n{deliv}\n\n"
            "Review for:\n"
            "1. Does it meet ALL requirements in the job description?\n"
            "2. Completeness: anything missing or half-done?\n"
            "3. Quality: is this professional-grade work?\n"
            "4. Would you pay full price for this?\n\n"
            "Score: PASS / NEEDS_WORK / FAIL\n"
            "If not PASS, list exact issues."
        )

        try:
            paid_router = LLMRouter(self._config)
            eco = EcoRouter(self._config, paid_router)
            response = await eco.chat(
                messages=[
                    LLMMessage(role="system", content="You are a strict quality reviewer."),
                    LLMMessage(role="user", content=review_prompt),
                ],
                user_id=user_id,
            )
            text = response.content
        except Exception as exc:
            logger.warning("Text review LLM call failed: %s", exc)
            return "Could not complete review — LLM call failed. Try again."

        if "PASS" in text.upper():
            return f"Review: PASS\n\n{text}\n\nReady to submit."
        return f"Review: NEEDS WORK\n\n{text}\n\nFix issues before submitting."

    def _find_claude_tool(self):
        """Find Claude Code MCP tool in registry."""
        if self._registry is None:
            return None
        for tool_name in ("mcp_claude_code", "claude_code"):
            tool = self._registry.get(tool_name)
            if tool is not None:
                return tool
        return None

    async def _call_claude(self, tool, user_id: str, prompt: str) -> str:
        """Call Claude Code MCP and return string response."""
        try:
            result = await tool.execute(user_id, {"prompt": prompt})
            return result if isinstance(result, str) else str(result)
        except Exception as exc:
            logger.warning("Claude Code MCP call failed: %s", exc)
            return "FAIL: Claude Code MCP call failed."
