"""Search for freelance jobs via JobSpy MCP, browser, or web search."""

from __future__ import annotations

import json
import logging
import re

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_SEARCH_PREFIX = "SURVIVAL_SEARCH:"


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
            "Search for jobs using JobSpy (Indeed, Glassdoor, ZipRecruiter, LinkedIn, Google). "
            "This is THE tool for job searching — do NOT use browser or delegate to specialists. "
            "Call this directly. Returns ranked matches with relevance scores. "
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
        from lazyclaw.survival.gig import create_gig
        from lazyclaw.survival.matcher import score_job
        from lazyclaw.survival.platforms import BROWSER_PLATFORMS, PLATFORMS
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)
        keywords = params.get("keywords", "") or " ".join(profile.skills[:5])
        try:
            max_results = min(int(params.get("max_results", 10)), 50)
        except (ValueError, TypeError):
            logger.debug("Invalid max_results param, defaulting to 10")
            max_results = 10

        if not keywords:
            return (
                "No search keywords and no skills profile set. "
                "Set your profile first: 'my skills are python, fastapi, react'"
            )

        all_jobs: list[dict] = []

        browser_platforms = [p for p in profile.platforms if p in BROWSER_PLATFORMS]

        # ALWAYS try JobSpy first — fast, free, no browser
        await self._ensure_jobspy_connected(user_id)
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
                    logger.debug("Failed to parse MCP job search results JSON", exc_info=True)
                    mcp_jobs = []
            else:
                mcp_jobs = mcp_result if isinstance(mcp_result, list) else []
            all_jobs.extend(mcp_jobs)

        # Browser path — Upwork, etc. (slower, needs login)
        # Skip if JobSpy already found enough results (browser is slow + Cloudflare)
        if not all_jobs:
            for platform_name in browser_platforms:
                platform = PLATFORMS.get(platform_name)
                if not platform:
                    continue
                browser_jobs = await self._search_via_browser(
                    user_id, keywords, platform
                )
                all_jobs.extend(browser_jobs)

        # Fallback: web search
        if not all_jobs and not browser_platforms:
            return await self._search_via_web(user_id, keywords)

        if not all_jobs:
            platforms_str = ", ".join(profile.platforms) or "configured platforms"
            return f"No jobs found for '{keywords}' on {platforms_str}."

        # Score and rank
        scored = [score_job(j, profile) for j in all_jobs]
        scored.sort(key=lambda j: j.match_score, reverse=True)
        threshold = 0.3 if browser_platforms else 0.7
        good = [j for j in scored if j.match_score >= threshold]

        if not good:
            return (
                f"Found {len(all_jobs)} jobs but none matched your profile well enough. "
                "Try broader keywords or lower your minimum rate."
            )

        # Create gig records for found jobs
        created_count = 0
        for job in good[:5]:
            try:
                await create_gig(
                    self._config, user_id,
                    platform=job.platform or "unknown",
                    title=job.title,
                    description=job.description[:500] if job.description else "",
                    budget=job.budget or "N/A",
                    url=job.url or "",
                    external_job_id=job.job_id or "",
                    status="found",
                )
                created_count += 1
            except Exception as exc:
                logger.warning("Failed to create gig record: %s", exc)

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

        lines.append(
            "Reply with a number to apply, or 'skip all'.\n"
            "DO NOT search more — these are the results. Wait for the user to choose."
        )

        # Best-effort Telegram push so the user is notified even when the
        # search was triggered from a background job or the web UI.
        # Failures are swallowed inside push_telegram — search succeeds
        # regardless of Telegram status.
        if created_count:
            try:
                from lazyclaw.notifications.push import push_telegram

                await push_telegram(
                    self._config,
                    "\n".join(lines),
                )
            except Exception as exc:
                logger.debug("Job-match Telegram push skipped: %s", exc)

        # Store displayed jobs for apply_job index matching
        search_data = json.dumps([
            {
                "job_id": j.job_id,
                "title": j.title,
                "platform": j.platform,
                "budget": j.budget,
                "url": j.url,
                "score": j.match_score,
                "description": j.description[:200] if j.description else "",
            }
            for j in good[:5]
        ])
        await save_memory(
            self._config, user_id,
            f"{_SEARCH_PREFIX}{search_data}",
            memory_type="survival",
            importance=3,
        )

        return "\n".join(lines)

    # -- JobSpy helpers --------------------------------------------------------

    async def _ensure_jobspy_connected(self, user_id: str) -> None:
        """On-demand connect mcp-jobspy if not already active."""
        try:
            import asyncio

            from lazyclaw.mcp.bridge import register_mcp_tools
            from lazyclaw.mcp.manager import (
                _active_clients,
                connect_server,
                get_server_id_by_name,
            )

            for tool_info in (self._registry.list_mcp_tools() if self._registry else []):
                func = tool_info.get("function", {})
                if "jobspy" in func.get("name", "").lower():
                    return

            sid = await get_server_id_by_name(self._config, user_id, "mcp-jobspy")
            if not sid:
                return
            if sid in _active_clients:
                return

            logger.warning("On-demand connecting mcp-jobspy (id=%s)...", sid[:8])
            client = await asyncio.wait_for(
                connect_server(self._config, user_id, sid), timeout=15,
            )
            count = await register_mcp_tools(
                client, self._registry, config=self._config, user_id=user_id,
            )
            logger.warning("On-demand connected mcp-jobspy: %d tools registered", count)
        except Exception as exc:
            logger.warning("Failed to on-demand connect mcp-jobspy: %s", exc)

    async def _try_jobspy(
        self, user_id: str, keywords: str, max_results: int,
    ) -> list | dict | str | None:
        """Try JobSpy MCP, then direct python-jobspy import."""
        registry = self._registry
        if registry is None:
            return await self._try_jobspy_direct(keywords, max_results)

        tool = None
        for tool_info in registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = func.get("name", "")
            tdesc = func.get("description", "").lower()
            if "jobspy" in tname.lower() or "jobspy" in tdesc:
                tool = registry.get(tname)
                if tool is not None:
                    break

        if tool is not None:
            try:
                logger.warning("Calling JobSpy MCP tool: %s", tool.name)
                return await tool.execute(user_id, {
                    "search_term": keywords,
                    "results_wanted": max_results,
                    "hours_old": 72,
                })
            except Exception as exc:
                logger.warning("JobSpy MCP call failed: %s", exc)

        logger.warning("JobSpy MCP not available — trying direct import")
        return await self._try_jobspy_direct(keywords, max_results)

    async def _try_jobspy_direct(
        self, keywords: str, max_results: int,
    ) -> str | None:
        """Call python-jobspy directly without MCP server."""
        try:
            import asyncio

            from jobspy import scrape_jobs

            loop = asyncio.get_running_loop()
            df = await loop.run_in_executor(
                None,
                lambda: scrape_jobs(
                    site_name=["indeed", "glassdoor"],
                    search_term=keywords,
                    location="Remote",
                    results_wanted=min(max_results, 50),
                    hours_old=72,
                ),
            )
            if df is None or df.empty:
                return None

            jobs = []
            for _, row in df.iterrows():
                job = {
                    "id": str(row.get("id", "")),
                    "title": str(row.get("title", "")),
                    "company": str(row.get("company_name", row.get("company", ""))),
                    "location": str(row.get("location", "")),
                    "site": str(row.get("site", "")),
                    "url": str(row.get("job_url", row.get("link", ""))),
                    "description": str(row.get("description", ""))[:500],
                }
                min_sal = row.get("min_amount", row.get("salary_min", None))
                max_sal = row.get("max_amount", row.get("salary_max", None))
                if min_sal and max_sal:
                    job["budget"] = f"${min_sal}-${max_sal}"
                jobs.append(job)

            logger.warning("JobSpy direct: found %d jobs", len(jobs))
            return json.dumps({"jobs": jobs})
        except ImportError:
            logger.debug("jobspy package not available for direct search")
            return None
        except Exception as exc:
            logger.warning("JobSpy direct search failed: %s", exc)
            return None

    # -- Fallback helpers ------------------------------------------------------

    async def _search_via_web(self, user_id: str, keywords: str) -> str:
        registry = self._registry
        if registry is None:
            return "No job search tools available."

        web_search = registry.get("web_search")
        if web_search is not None:
            try:
                results = await web_search.execute(user_id, {
                    "query": f"site:upwork.com {keywords} freelance job",
                })
                return f"Web search results:\n\n{results}"
            except Exception as exc:
                logger.warning("Web search fallback failed: %s", exc)

        return "No job search tools available."

    async def _search_via_browser(
        self, user_id: str, keywords: str, platform,
    ) -> list[dict]:
        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            return []

        search_url = platform.base_url + platform.search_path.format(
            keywords=keywords.replace(" ", "%20"),
        )

        try:
            await browser.execute(user_id, {"action": "open", "url": search_url})
            page_text = await browser.execute(user_id, {"action": "read"})

            if not page_text or len(page_text) < 100:
                logger.warning("Empty page from %s — may need login", platform.name)
                return []

            if platform.name == "Upwork":
                return _parse_upwork_jobs(page_text)
            return _parse_generic_jobs(page_text, platform.name)
        except Exception as exc:
            logger.warning("Browser search on %s failed: %s", platform.name, exc)
            return []


# -- Parsers (module-level) ------------------------------------------------

def _parse_upwork_jobs(page_text: str) -> list[dict]:
    jobs: list[dict] = []
    sections = re.split(r'(?=link\s+"[^"]*"\s+url="/jobs/~)', page_text)

    for section in sections:
        if "/jobs/~" not in section:
            continue
        url_match = re.search(r'url="(/jobs/~\w+)"', section)
        if not url_match:
            continue
        job_path = url_match.group(1)
        job_id = job_path.split("~")[-1] if "~" in job_path else job_path

        title_match = re.search(r'(?:heading[^"]*"|link\s+")(.*?)"', section)
        title = title_match.group(1) if title_match else "Untitled"

        budget = "N/A"
        budget_match = re.search(
            r"\$[\d,.]+(?:\s*[-\u2013]\s*\$[\d,.]+)?(?:/hr)?", section,
        )
        if budget_match:
            budget = budget_match.group(0)

        desc = ""
        desc_match = re.search(r'text\s+"([^"]{50,})"', section)
        if desc_match:
            desc = desc_match.group(1)[:500]

        jobs.append({
            "id": job_id,
            "title": title,
            "company": "",
            "location": "Remote",
            "site": "upwork",
            "platform": "upwork",
            "url": f"https://www.upwork.com{job_path}",
            "description": desc,
            "budget": budget,
            "salary": budget,
        })

    return jobs


def _parse_generic_jobs(page_text: str, platform_name: str) -> list[dict]:
    jobs: list[dict] = []
    links = re.findall(r'link\s+"([^"]+)"\s+url="([^"]+)"', page_text)

    _SKIP = frozenset({
        "sign in", "log in", "home", "menu", "search", "filter",
        "about", "help", "contact", "privacy", "terms",
    })

    for title, url in links:
        if len(title) < 10 or len(title) > 200:
            continue
        if any(skip in title.lower() for skip in _SKIP):
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
        })

    return jobs[:20]
