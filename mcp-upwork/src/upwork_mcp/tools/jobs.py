"""Job search and details tools for Upwork MCP."""

import re
import asyncio
import urllib.parse
from pydantic import BaseModel, Field
from ..browser.client import _is_nav_noise, get_browser


def _clean_posted(raw: str) -> str:
    """Strip 'Posted' prefix + collapse whitespace from a posted-time string.

    Upwork's search results render this as:
        "Posted\\n            \\n              6 hours ago"
    We want: "6 hours ago".
    """
    if not raw:
        return ""
    text = " ".join(raw.split())
    if text.lower().startswith("posted"):
        text = text[len("posted"):].lstrip(" :-")
    return text.strip()


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

    # Build search URL.
    #
    # /nx/find-work/best-matches is Upwork's personalized recommendations
    # feed — ~20-50 jobs that Upwork picks for the user. Adding ?q=...
    # filters within THAT small set, not across the open job board. That's
    # why every search came back with the same 2 jobs regardless of
    # keyword change.
    #
    # /nx/search/jobs/ is the global keyword search across the full board
    # (100k+ active postings). That's what users actually want when they
    # say "find me python upwork jobs".
    base_url = "https://www.upwork.com/nx/search/jobs/"
    query_params = {"q": params.query}

    if params.job_type:
        query_params["t"] = "0" if params.job_type.lower() == "hourly" else "1"

    if params.experience_level:
        level_map = {"entry": "1", "intermediate": "2", "expert": "3"}
        level = level_map.get(params.experience_level.lower())
        if level:
            query_params["contractor_tier"] = level

    url = f"{base_url}?{urllib.parse.urlencode(query_params)}"
    # safe_goto picks an existing Upwork tab (cookies + warm session),
    # serializes concurrent navigations under _NAV_LOCK, and waits out
    # Cloudflare's JS challenge before returning the page.
    page = await browser.safe_goto(url)
    await asyncio.sleep(3)

    jobs = []
    seen_urls: set[str] = set()

    # Get job tiles. The 2026 search page (/nx/search/jobs/) renders each
    # tile as <article data-test="JobTile">, NOT the old <section> wrapper
    # the /best-matches page used. Falling back to <section> here meant
    # we matched only the page-level <section> (count=1) and returned 0
    # jobs from the new URL. Try the new selectors first; <section> is
    # kept as a last-resort fallback in case Upwork ships a third layout.
    #
    # NOTE: the comma-separated list matches each tile multiple times
    # (once per matching selector), so 10 tiles → 30 hits. We dedupe by
    # url further down in the extraction loop so each unique job only
    # appears once in the returned list.
    sections = await page.query_selector_all(
        'article[data-test="JobTile"], article, [data-test="JobTile"], section'
    )

    for section in sections[:params.limit * 6]:  # 3× overlap from selector list, plus headroom
        try:
            job = {}

            # Title link. The 2026 search results use <h2 a href="/jobs/...">;
            # /best-matches used <h3 a> or <h4 a>; keep all three for resilience.
            title_link = await section.query_selector(
                "h2 a[href*='/jobs/'], h3 a[href*='/jobs/'], h4 a[href*='/jobs/'], "
                "h2 a, h3 a, h4 a"
            )
            if not title_link:
                continue

            title = await title_link.text_content()
            href = await title_link.get_attribute("href")

            if not title or not href or "/jobs/" not in href:
                continue

            full_url = (
                f"https://www.upwork.com{href}" if href.startswith("/") else href
            )
            # Canonicalize for dedup: strip query string + trailing slash
            # so /jobs/x_~01abc and /jobs/x_~01abc/?ref=foo collapse to the
            # same key. The full URL with query string still goes into the
            # returned payload so callers can preserve referrer tracking.
            dedup_key = full_url.split("?", 1)[0].rstrip("/")
            if dedup_key in seen_urls:
                continue
            seen_urls.add(dedup_key)

            job["title"] = title.strip()
            job["url"] = full_url

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

            # Get skills. Filter out Upwork's accessibility nav labels
            # ("Skip skills" / "Previous skills. Update list") that the
            # button/[class*=token] selectors used to capture from the
            # skill-section navigation chrome.
            skill_els = await section.query_selector_all("button, [class*='skill'], [class*='token']")
            skills: list[str] = []
            seen_skills: set[str] = set()
            for el in skill_els[:24]:
                text = await el.text_content()
                if not text:
                    continue
                candidate = text.strip()
                if not (1 < len(candidate) < 30):
                    continue
                if _is_nav_noise(candidate):
                    continue
                key = candidate.lower()
                if key in seen_skills:
                    continue
                seen_skills.add(key)
                skills.append(candidate)
            if skills:
                job["skills"] = skills

            # Get posted time — strip the "Posted" prefix and collapse
            # the whitespace Upwork bakes into the rendered timestamp.
            # Raw is "Posted\n   \n   6 hours ago" → "6 hours ago".
            time_els = await section.query_selector_all("span, small")
            for el in time_els:
                text = await el.text_content()
                if text and ("ago" in text.lower() or "posted" in text.lower()):
                    job["posted"] = _clean_posted(text)
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

    # Normalize URL to the canonical /jobs/~<id> form. Upwork exposes the
    # same posting under several paths (verified live 2026-05-11):
    #   /jobs/<title>_~<id>/                      → canonical, all extractors work
    #   /jobs/~<id>                               → canonical short form
    #   /freelance-jobs/apply/<title>_~<id>/      → apply-page layout, h1/data-cy
    #                                              selectors miss → empty title
    #                                              → cache layer flags as error
    #                                              → brain retries forever
    #   /jobs/<title>_~<id>/?referrer_url_path=…  → canonical + tracking
    # The pattern is always "~<digits>" somewhere in the path. Extract that
    # ID and always rebuild as the canonical short form so we hit the
    # 2026 Air3 layout the rest of the extractors target.
    raw = params.job_url
    id_match = re.search(r"~(\d{10,})", raw)
    if id_match:
        url = f"https://www.upwork.com/jobs/~{id_match.group(1)}"
    elif not raw.startswith("http"):
        # Bare ID passed (legacy callers) — treat as ~<id>.
        ident = raw.lstrip("~")
        url = f"https://www.upwork.com/jobs/~{ident}"
    else:
        # Already an http(s) URL but no ~<id> we recognize — let it through
        # and surface whatever the page returns. Final empty-title guard
        # below will return a structured error if extraction fails.
        url = raw

    # safe_goto: prefer existing Upwork tab + warm session + Cloudflare
    # retry. Eliminates the "cloudflare_blocked" failure on /jobs/~<id>
    # that fires when Playwright lands on a cold tab.
    page = await browser.safe_goto(url)
    await asyncio.sleep(3)

    job = {"url": url}

    # Title — Upwork's 2026 layout uses a few different attribute hooks
    # depending on whether the post is rendered in its modern Air3 layout
    # or the legacy fallback. Try them in order, then h1 only as a last
    # resort (h1 on /freelancers/settings/profile would match "Settings",
    # but on a /jobs/~<id> page it should match the actual posting title).
    job["title"] = await _first_text(page, [
        '[data-cy="job-title"]',
        '[data-test="job-title"]',
        '[data-test="JobTitle"]',
        '[data-qa="job-title"]',
        '[data-test*="JobTitle"]',
        'header h1',
        'h1',
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
