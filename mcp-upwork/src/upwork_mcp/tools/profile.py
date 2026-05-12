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
    browser = get_browser()
    await browser.ensure_logged_in()

    # Navigate to connects page via safe_goto (Cloudflare-resilient,
    # serialized under _NAV_LOCK so a parallel get_messages call can't
    # collapse the tab mid-extract).
    page = await browser.safe_goto("https://www.upwork.com/nx/plans/connects/balance")

    connects = {}

    # Available connects
    available_el = await page.query_selector('[data-test="connects-available"], .connects-balance, [data-cy="available-connects"]')
    if available_el:
        text = (await available_el.text_content() or "").strip()
        # Extract number
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            connects["available"] = int(numbers[0])

    # If we couldn't find it, try the header/sidebar on main page
    if "available" not in connects:
        page = await browser.safe_goto("https://www.upwork.com/nx/find-work/")
        connects_el = await page.query_selector('[data-test="connects-count"], .connects-count')
        if connects_el:
            text = (await connects_el.text_content() or "").strip()
            import re
            numbers = re.findall(r'\d+', text)
            if numbers:
                connects["available"] = int(numbers[0])

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
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to work diary or stats page
    await page.goto("https://www.upwork.com/nx/wm/contracts", wait_until="networkidle")

    stats = {}

    # Total earnings
    earnings_el = await page.query_selector('[data-test="total-earnings"], .earnings-total')
    if earnings_el:
        stats["total_earnings"] = (await earnings_el.text_content() or "").strip()

    # Active contracts count
    active_el = await page.query_selector('[data-test="active-contracts"], .active-count')
    if active_el:
        stats["active_contracts"] = (await active_el.text_content() or "").strip()

    # Total hours
    hours_el = await page.query_selector('[data-test="total-hours"], .hours-total')
    if hours_el:
        stats["total_hours"] = (await hours_el.text_content() or "").strip()

    # Jobs completed
    jobs_el = await page.query_selector('[data-test="jobs-completed"], .jobs-count')
    if jobs_el:
        stats["jobs_completed"] = (await jobs_el.text_content() or "").strip()

    return stats
