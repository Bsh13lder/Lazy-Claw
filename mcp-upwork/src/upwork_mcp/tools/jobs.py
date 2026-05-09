"""Job search and details tools for Upwork MCP."""

import re
import asyncio
import urllib.parse
from pydantic import BaseModel, Field
from ..browser.client import get_browser


class JobSearchParams(BaseModel):
    """Parameters for job search."""
    query: str = Field(description="Search keywords")
    experience_level: str | None = Field(
        default=None,
        description="Experience level: entry, intermediate, or expert"
    )
    job_type: str | None = Field(
        default=None,
        description="Job type: hourly or fixed"
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of results")


class JobDetailsParams(BaseModel):
    """Parameters for getting job details."""
    job_url: str = Field(description="Full Upwork job URL or job ID")


async def search_jobs(params: JobSearchParams) -> list[dict]:
    """Search for jobs on Upwork matching the specified criteria.

    Returns a list of job summaries with title, budget, and URL.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Build search URL
    base_url = "https://www.upwork.com/nx/find-work/best-matches"
    query_params = {"q": params.query}

    if params.job_type:
        query_params["t"] = "0" if params.job_type.lower() == "hourly" else "1"

    if params.experience_level:
        level_map = {"entry": "1", "intermediate": "2", "expert": "3"}
        level = level_map.get(params.experience_level.lower())
        if level:
            query_params["contractor_tier"] = level

    url = f"{base_url}?{urllib.parse.urlencode(query_params)}"
    await page.goto(url, wait_until="networkidle")
    await asyncio.sleep(3)

    jobs = []

    # Get job sections (each section contains one job)
    sections = await page.query_selector_all("section")

    for section in sections[:params.limit * 2]:  # Check more sections
        try:
            job = {}

            # Get title from h3 or h4 link
            title_link = await section.query_selector("h3 a, h4 a")
            if not title_link:
                continue

            title = await title_link.text_content()
            href = await title_link.get_attribute("href")

            if not title or not href or "/jobs/" not in href:
                continue

            job["title"] = title.strip()
            job["url"] = f"https://www.upwork.com{href}" if href.startswith("/") else href

            # Get description snippet
            desc_el = await section.query_selector("p, [data-test='job-description-text']")
            if desc_el:
                desc = await desc_el.text_content()
                if desc:
                    job["description"] = desc.strip()[:300]

            # Get budget/rate info
            for sel in ["strong", "span"]:
                els = await section.query_selector_all(sel)
                for el in els:
                    text = await el.text_content()
                    if text and ("$" in text or "hourly" in text.lower() or "fixed" in text.lower()):
                        job["budget"] = text.strip()
                        break
                if "budget" in job:
                    break

            # Get skills
            skill_els = await section.query_selector_all("button, [class*='skill'], [class*='token']")
            skills = []
            for el in skill_els[:8]:
                text = await el.text_content()
                if text and len(text.strip()) > 1 and len(text.strip()) < 30:
                    skills.append(text.strip())
            if skills:
                job["skills"] = skills

            # Get posted time
            time_els = await section.query_selector_all("span, small")
            for el in time_els:
                text = await el.text_content()
                if text and ("ago" in text.lower() or "posted" in text.lower()):
                    job["posted"] = text.strip()
                    break

            jobs.append(job)

            if len(jobs) >= params.limit:
                break

        except Exception:
            continue

    return jobs


async def _first_text(page, selectors: list[str]) -> str:
    """Return the first non-empty text from the given selector list."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
        except Exception:
            continue
        if not el:
            continue
        txt = (await el.text_content() or "").strip()
        if txt:
            return txt
    return ""


async def _all_text(page, selector: str, max_items: int = 20) -> list[str]:
    """Return cleaned text from up to max_items elements matching selector."""
    out: list[str] = []
    try:
        els = await page.query_selector_all(selector)
    except Exception:
        return out
    for el in els[:max_items]:
        txt = await el.text_content()
        if txt:
            txt = txt.strip()
            if txt:
                out.append(txt)
    return out


async def get_job_details(params: JobDetailsParams) -> dict:
    """Get detailed information about a specific Upwork job posting.

    Returns comprehensive job details including description, client history,
    skills required, and application requirements.

    Selectors target Upwork's 2026 Air3 design system landmarks (data-cy /
    data-test / data-qa). Avoids blind ``p, span, div`` scans that previously
    leaked raw JS/CSS into the description and budget fields.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Normalize URL
    url = params.job_url
    if not url.startswith("http"):
        url = f"https://www.upwork.com/jobs/{url}"

    await page.goto(url, wait_until="networkidle")
    await asyncio.sleep(3)

    job = {"url": url}

    # Title — Upwork uses [data-cy="job-title"] on freelancer-facing detail pages.
    # h1 is the safe fallback; h2 is NOT (it pulls section headings).
    job["title"] = await _first_text(page, [
        '[data-cy="job-title"]',
        '[data-test="job-title"]',
        "h1",
    ])

    # Description — capital-D [data-test="Description"] is the actual section.
    # The lowercase variant and ".description" selectors fired before but
    # matched outer wrappers that included sidebar JS.
    desc = await _first_text(page, [
        '[data-test="Description"] .text-body-sm',
        '[data-test="Description"] [data-test*="Body"]',
        '[data-test="Description"]',
        '[data-cy="description"]',
    ])
    if desc:
        # Strip the literal "Summary" header Upwork prepends to the section
        # and trim to a sane size for downstream LLM consumption.
        if desc.lower().startswith("summary"):
            desc = desc[len("summary"):].lstrip()
        job["description"] = desc[:6000]

    # Budget block — distinct selectors for fixed-price vs hourly.
    # Only set project_type from explicit job-info landmarks, never from
    # blind page-wide ``li`` scans — the navbar carries a generic "Hourly
    # work diary" pill that previously poisoned every fixed-price job
    # with project_type="hourly". Leave the field unset when uncertain
    # so callers fall back to sniffing the apply-page form.
    fixed_text = await _first_text(page, [
        '[data-test="BudgetAmount"]',
        '[data-cy="budget-amount"]',
    ])
    hourly_range = await _first_text(page, [
        '[data-test="HourlyBudget"]',
        '[data-cy="clock-hourly"] + *',
    ])
    if fixed_text:
        job["budget"] = fixed_text
        job["project_type"] = "fixed"
    elif hourly_range:
        job["budget"] = hourly_range
        job["project_type"] = "hourly"

    # Experience level — from the dedicated pill, not blind text scan
    exp = await _first_text(page, [
        '[data-test="ContractorTier"]',
        '[data-cy="experience"]',
    ])
    if exp:
        job["experience_level"] = exp

    # Project length / duration
    duration = await _first_text(page, [
        '[data-test="Duration"]',
        '[data-cy="duration"]',
    ])
    if duration:
        job["project_length"] = duration

    # Posted timestamp
    posted = await _first_text(page, [
        '[data-test="PostedOn"]',
        '[data-test="Posted"]',
    ])
    if posted:
        job["posted"] = posted

    # Skills/tags — Upwork's actual skill-token selector
    skills = await _all_text(page, '[data-test="Skill"], [data-test="Tag-tag"]', max_items=15)
    # Dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for s in skills:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    if deduped:
        job["skills"] = deduped[:10]

    # Client info via data-qa attributes (verified live on /jobs/<id> pages)
    client: dict = {}
    location = await _first_text(page, ['[data-qa="client-location"]'])
    if location:
        client["location"] = location

    jobs_posted = await _first_text(page, ['[data-qa="client-job-posting-stats"]'])
    if jobs_posted:
        client["jobs_posted"] = jobs_posted

    verification = await _first_text(page, ['[data-qa="client-verification-status"]'])
    if verification:
        client["payment_verified"] = "verified" in verification.lower() and "not" not in verification.lower()

    spent = await _first_text(page, ['[data-qa="client-total-spent"]'])
    if spent:
        m = re.search(r"\$[\d,]+(?:\.\d+)?[KMB]?\+?", spent)
        if m:
            client["total_spent"] = m.group(0)

    rating = await _first_text(page, ['[data-qa="client-feedback"], [data-test="ClientFeedback"]'])
    if rating:
        m = re.search(r"\d\.\d{1,2}", rating)
        if m:
            client["rating"] = m.group(0)

    if client:
        job["client"] = client

    # Connects required — narrow selector, only inside the apply button area
    connects_text = await _first_text(page, [
        '[data-test="ConnectsAmount"]',
        '[data-cy="connects"]',
    ])
    if connects_text:
        m = re.search(r"\d+", connects_text)
        if m:
            job["connects_required"] = int(m.group(0))

    # Fail-fast guard: if neither title nor description came through, the
    # page never rendered (Cloudflare wall, login redirect, or 404). Raise
    # so the caller sees an exception — returning an empty dict makes the
    # lazyclaw skill_lesson auto-recorder mark the call "pending" (success-
    # like), and the agent retries the same dead path N times waiting for
    # fields that will never arrive.
    if not job.get("title") and not job.get("description"):
        try:
            current_url = page.url
        except Exception:
            current_url = url
        try:
            page_html = (await page.content() or "")[:300]
        except Exception:
            page_html = ""
        cloudflare_hit = (
            "challenges.cloudflare.com" in current_url
            or "just a moment" in page_html.lower()
            or "cf-browser-verification" in page_html.lower()
        )
        login_redirect = "/ab/account-security/login" in current_url
        if cloudflare_hit:
            reason = "cloudflare_blocked"
        elif login_redirect:
            reason = "login_required"
        else:
            reason = "page_empty"
        raise RuntimeError(
            f"upwork_get_job_details {reason}: page returned no title or "
            f"description. Final URL: {current_url}. The unauthenticated "
            f"headless browser cannot pass Upwork's gate. Switch to the "
            f"host browser bridge (use_host_browser) so your real cookies "
            f"come along, or call browser(action='open', url=...) and read "
            f"from your VNC takeover session."
        )

    return job
