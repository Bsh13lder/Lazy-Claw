"""Profile and connects tools for Upwork MCP."""

import logging

from ..browser.client import _is_nav_noise, get_browser

logger = logging.getLogger(__name__)


async def get_my_profile() -> dict:
    """Get your Upwork freelancer profile information.

    Returns profile data including name, title, hourly rate, JSS score,
    availability status, and skill tags.
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    # safe_goto serializes navigation + clears Cloudflare. The old direct
    # page.goto landed on the settings page before content rendered and
    # fell through to the `h1` fallback selector, which matches the
    # literal page heading "Settings". The agent then wrote "Settings"
    # to the user's display_name via the auto-pull skill — every
    # subsequent proposal was signed off "— Settings".
    page = await browser.safe_goto(
        "https://www.upwork.com/freelancers/settings/profile"
    )

    profile = {}

    # Name. The `h1` fallback is intentionally NOT here — h1 on the
    # settings page is the word "Settings", which we'd capture as the
    # user's name. Only structured selectors. If they all miss, the
    # caller surfaces an error rather than storing a nav label.
    name_el = await page.query_selector(
        '[data-test="profile-name"], [data-qa="user-name"], .profile-name, '
        '[data-cy="profile-name"]'
    )
    if name_el:
        profile["name"] = (await name_el.text_content() or "").strip()

    # Professional title
    title_el = await page.query_selector('[data-test="profile-title"], .profile-title, [data-cy="title"]')
    if title_el:
        profile["title"] = (await title_el.text_content() or "").strip()

    # Hourly rate
    rate_el = await page.query_selector('[data-test="hourly-rate"], .hourly-rate, [data-cy="rate"]')
    if rate_el:
        profile["hourly_rate"] = (await rate_el.text_content() or "").strip()

    # Profile overview/bio
    overview_el = await page.query_selector('[data-test="profile-overview"], .profile-overview, [data-cy="overview"]')
    if overview_el:
        profile["overview"] = (await overview_el.text_content() or "").strip()

    # Skills. Filter out Upwork's accessibility-skip nav labels
    # ("Skip skills", "Previous skills. Update list") that the .air3-token
    # selector used to capture from the page's nav region. Real skill
    # chips never have those exact strings.
    skill_els = await page.query_selector_all('[data-test="skill"], .skill-badge, .air3-token')
    skill_list: list[str] = []
    for el in skill_els:
        text = await el.text_content()
        if not text:
            continue
        candidate = text.strip()
        if not candidate or _is_nav_noise(candidate):
            continue
        skill_list.append(candidate)
    profile["skills"] = skill_list

    # Now get stats from a different page
    page = await browser.safe_goto("https://www.upwork.com/nx/find-work/best-matches")

    # Try to get JSS from sidebar or header
    jss_el = await page.query_selector('[data-test="jss"], .jss-score, [data-cy="jss"]')
    if jss_el:
        profile["job_success_score"] = (await jss_el.text_content() or "").strip()

    # Availability badge
    avail_el = await page.query_selector('[data-test="availability"], .availability-badge')
    if avail_el:
        profile["availability"] = (await avail_el.text_content() or "").strip()

    # Profile completeness
    complete_el = await page.query_selector('[data-test="profile-completeness"], .profile-complete')
    if complete_el:
        profile["profile_completeness"] = (await complete_el.text_content() or "").strip()

    # Get connects balance
    connects = await get_connects_balance()
    profile["connects"] = connects

    # Defensive guard: if the scraped name is a known Upwork nav label
    # ("Settings", "Edit Profile", "My Profile", etc.) drop it. The
    # auto-pull skill in lazyclaw treats empty name as "skip" and never
    # writes garbage to the user's stored display_name. Same for title.
    if profile.get("name") and _is_nav_noise(profile["name"]):
        logger.warning(
            "upwork_get_my_profile: dropped nav-label name %r — selectors "
            "missed the real profile-name element", profile["name"],
        )
        profile.pop("name", None)
    if profile.get("title") and _is_nav_noise(profile["title"]):
        logger.warning(
            "upwork_get_my_profile: dropped nav-label title %r", profile["title"],
        )
        profile.pop("title", None)

    return profile


async def get_connects_balance() -> dict:
    """Get current Upwork Connects balance and usage.

    Returns the number of available connects, pending connects,
    and connects balance details.
    """
    import re

    browser = get_browser()
    await browser.ensure_logged_in()

    # 2026 layout: /nx/plans/connects/balance returns 404; the actual
    # page is /nx/plans/connects/history. Body text shape (live probe
    # 2026-05-12) is "...My balance28 ConnectsBuy Connects..." — no
    # structured attribute hooks (no data-test="connects-balance" etc),
    # so we extract the digit from the readable text following "My
    # balance" in <main>.
    page = await browser.safe_goto("https://www.upwork.com/nx/plans/connects/history")

    connects: dict = {}
    main_el = await page.query_selector("main")
    if main_el:
        main_text = (await main_el.text_content() or "")
        m = re.search(r"My balance\s*(\d+)\s*Connects?", main_text, re.IGNORECASE)
        if m:
            connects["available"] = int(m.group(1))
        else:
            # Fallback: first "<number> Connects" pattern anywhere in main
            m2 = re.search(r"(\d+)\s*Connects?\b", main_text)
            if m2:
                connects["available"] = int(m2.group(1))

    # Header-pill fallback (e.g. when /connects/history itself is down)
    if "available" not in connects:
        page = await browser.safe_goto("https://www.upwork.com/nx/find-work/")
        for sel in (
            '[data-test="connects-count"]',
            '.connects-count',
            '[data-test="connects-amount"]',
            '[data-cy="connects"]',
        ):
            el = await page.query_selector(sel)
            if not el:
                continue
            text = (await el.text_content() or "").strip()
            digits = re.findall(r"\d+", text)
            if digits:
                connects["available"] = int(digits[0])
                break

    # Try to get additional connects info
    pending_el = await page.query_selector('[data-test="pending-connects"]')
    if pending_el:
        text = (await pending_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            connects["pending"] = int(numbers[0])

    return connects


async def get_profile_stats() -> dict:
    """Get profile statistics including earnings and work history.

    Returns stats like total earnings, hours worked, jobs completed.

    Returns ``{"status": "scrape_miss", ...}`` when every selector misses
    so the caller can distinguish "real zeros" from a stale-DOM scrape
    failure — verified live 2026-05-13: the 2026 layout dropped every
    legacy data-test attr on /nx/wm/contracts and this function silently
    returned ``{}`` for months, making "Total earnings: unknown" look
    indistinguishable from "I'm a new freelancer with nothing yet".
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    # safe_goto: Cloudflare-retried + serialized + tab-picker so we don't
    # land on a fresh tab that triggers Cloudflare while the user's real
    # Upwork tab sits idle on another origin.
    page = await browser.safe_goto("https://www.upwork.com/nx/wm/contracts")

    stats: dict = {}

    # Total earnings
    earnings_el = await page.query_selector(
        '[data-test="total-earnings"], [data-qa="total-earnings"], '
        '[data-test="earnings-total"], .earnings-total'
    )
    if earnings_el:
        stats["total_earnings"] = (await earnings_el.text_content() or "").strip()

    # Active contracts count
    active_el = await page.query_selector(
        '[data-test="active-contracts"], [data-qa="active-contracts"], '
        '[data-test="active-count"], .active-count'
    )
    if active_el:
        stats["active_contracts"] = (await active_el.text_content() or "").strip()

    # Total hours
    hours_el = await page.query_selector(
        '[data-test="total-hours"], [data-qa="total-hours"], '
        '[data-test="hours-total"], .hours-total'
    )
    if hours_el:
        stats["total_hours"] = (await hours_el.text_content() or "").strip()

    # Jobs completed
    jobs_el = await page.query_selector(
        '[data-test="jobs-completed"], [data-qa="jobs-completed"], '
        '[data-test="jobs-count"], .jobs-count'
    )
    if jobs_el:
        stats["jobs_completed"] = (await jobs_el.text_content() or "").strip()

    if not stats:
        try:
            current_url = page.url
        except Exception:
            current_url = "?"
        logger.warning(
            "get_profile_stats: all 4 selectors missed on %s — surfacing scrape_miss",
            current_url,
        )
        return {
            "status": "scrape_miss",
            "error": (
                "Profile stats selectors all missed on "
                f"{current_url}. Upwork may have redesigned /nx/wm/contracts "
                "or the page didn't fully render. Open Brave to verify."
            ),
            "page_url": current_url,
        }

    return stats
