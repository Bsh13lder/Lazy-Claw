"""Proposal tools for Upwork MCP."""

from pydantic import BaseModel, Field
from ..browser.client import get_browser


class ProposalsParams(BaseModel):
    """Parameters for getting proposals."""
    status: str = Field(
        default="active",
        description="Filter by status: active, submitted, archived, or all"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")


class SubmitProposalParams(BaseModel):
    """Parameters for submitting a proposal."""
    job_url: str = Field(description="Full Upwork job URL")
    cover_letter: str = Field(description="Cover letter content")
    rate: float | None = Field(default=None, description="Proposed hourly rate (for hourly jobs)")
    bid: float | None = Field(default=None, description="Bid amount (for fixed-price jobs)")
    answers: list[str] | None = Field(default=None, description="Answers to screening questions")


async def get_proposals(params: ProposalsParams) -> list[dict]:
    """Get your submitted proposals on Upwork.

    Returns a list of proposals with job title, status, bid amount, and dates.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to proposals page
    status_path = {
        "active": "active",
        "submitted": "submitted",
        "archived": "archived",
        "all": ""
    }.get(params.status.lower(), "active")

    url = f"https://www.upwork.com/nx/proposals/{'?status=' + status_path if status_path else ''}"
    await page.goto(url, wait_until="networkidle")

    proposals = []

    # Wait for proposals to load
    try:
        await page.wait_for_selector('[data-test="proposal-tile"], .proposal-row', timeout=10000)
    except Exception:
        # No proposals or different structure
        pass

    # Extract proposal cards
    proposal_els = await page.query_selector_all('[data-test="proposal-tile"], .proposal-row, article')

    for el in proposal_els[:params.limit]:
        try:
            proposal = await _extract_proposal(el)
            if proposal:
                proposals.append(proposal)
        except Exception:
            continue

    return proposals


async def _extract_proposal(el) -> dict | None:
    """Extract proposal data from element."""
    proposal = {}

    # Job title
    title_el = await el.query_selector('[data-test="job-title"], .job-title, a h3, h4')
    if title_el:
        proposal["job_title"] = (await title_el.text_content() or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            proposal["job_url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"

    if not proposal.get("job_title"):
        return None

    # Status
    status_el = await el.query_selector('[data-test="proposal-status"], .status-badge, .proposal-status')
    if status_el:
        proposal["status"] = (await status_el.text_content() or "").strip()

    # Bid/rate
    bid_el = await el.query_selector('[data-test="bid-amount"], .bid, .rate')
    if bid_el:
        proposal["bid"] = (await bid_el.text_content() or "").strip()

    # Submitted date
    date_el = await el.query_selector('[data-test="submitted-date"], .date, time')
    if date_el:
        proposal["submitted"] = (await date_el.text_content() or "").strip()

    # Client viewed
    viewed_el = await el.query_selector('[data-test="client-viewed"], .viewed')
    proposal["client_viewed"] = viewed_el is not None

    # Interview status
    interview_el = await el.query_selector('[data-test="interview-status"], .interview')
    if interview_el:
        proposal["interview_status"] = (await interview_el.text_content() or "").strip()

    # Connects used
    connects_el = await el.query_selector('[data-test="connects-used"], .connects')
    if connects_el:
        text = (await connects_el.text_content() or "").strip()
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            proposal["connects_used"] = int(numbers[0])

    return proposal


async def get_proposal_details(proposal_url: str) -> dict:
    """Get detailed information about a specific proposal.

    Args:
        proposal_url: URL to the proposal

    Returns details including cover letter, bid, and any messages.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto(proposal_url, wait_until="networkidle")

    details = {"url": proposal_url}

    # Job title
    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    if title_el:
        details["job_title"] = (await title_el.text_content() or "").strip()

    # Cover letter
    cover_el = await page.query_selector('[data-test="cover-letter"], .cover-letter')
    if cover_el:
        details["cover_letter"] = (await cover_el.text_content() or "").strip()

    # Bid/Rate
    bid_el = await page.query_selector('[data-test="bid-amount"], .bid-amount')
    if bid_el:
        details["bid"] = (await bid_el.text_content() or "").strip()

    # Status
    status_el = await page.query_selector('[data-test="proposal-status"], .status')
    if status_el:
        details["status"] = (await status_el.text_content() or "").strip()

    # Client response/messages
    messages = []
    message_els = await page.query_selector_all('[data-test="message"], .message-item')
    for el in message_els:
        msg_text = await el.text_content()
        if msg_text:
            messages.append(msg_text.strip())
    details["messages"] = messages

    return details


async def submit_proposal(params: SubmitProposalParams) -> dict:
    """Submit a proposal to an Upwork job.

    IMPORTANT: This is a sensitive action that will spend Connects.
    Make sure the cover letter and rate/bid are correct before submitting.

    Returns submission status and connects used.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to job page first
    await page.goto(params.job_url, wait_until="networkidle")

    # Wait for Cloudflare "Just a moment..." interstitial to clear.
    # Upwork wraps every fresh navigation in this challenge — networkidle
    # fires while the challenge is still showing, so we need an explicit
    # poll. Cleared either by automatic JS validation (~5s on most home
    # networks) or stays blocked if the IP/UA looks suspicious.
    cf_cleared = await _wait_for_cloudflare_clear(page, max_wait_s=20.0)
    if not cf_cleared:
        try:
            cur_title = await page.title()
        except Exception:
            cur_title = "?"
        return {
            "status": "error",
            "message": (
                f"Upwork is showing a Cloudflare 'Verify you are human' "
                f"challenge at {page.url} (title: {cur_title!r}). "
                f"Open Brave on the host, solve the challenge once, then "
                f"retry — Cloudflare keeps you cleared for ~30 minutes."
            ),
            "page_url": page.url,
            "cloudflare_blocked": True,
        }

    # If we landed directly on a proposal form (e.g. job_url passed was an
    # /apply/ URL, or the user re-opened a partial draft), skip the
    # Apply-button click and go straight to filling. The cover-letter
    # textarea is the most reliable sentinel for the proposal form.
    proposal_form_open = await page.query_selector(
        '[data-test="cover-letter-input"], textarea[aria-label*="Cover letter"], '
        'textarea[name*="cover"], [data-test="proposal-form"]'
    )

    if not proposal_form_open:
        # Try the explicit Apply Now / Submit a Proposal entry point.
        # Selector list deliberately fat — Upwork rotates data-test attrs
        # and button text faster than the upstream MCP can keep up.
        apply_btn = None
        apply_selectors = (
            '[data-test="apply-button"]',
            '[data-test="apply-now-button"]',
            '[data-test="submit-proposal-button"]',
            'a[data-test*="apply"]',
            'button[aria-label*="Apply"]',
            'button:has-text("Apply Now")',
            'button:has-text("Submit a Proposal")',
            'a:has-text("Apply Now")',
            'a:has-text("Submit a Proposal")',
        )
        for sel in apply_selectors:
            try:
                apply_btn = await page.query_selector(sel)
                if apply_btn:
                    break
            except Exception:
                continue

        if not apply_btn:
            # Detect the common "you've already applied" sentinel so the
            # error string tells the brain exactly what's going on.
            already_applied = False
            for sel in (
                'a:has-text("View Proposal")',
                'a:has-text("View proposal")',
                'button:has-text("Withdraw")',
                '[data-test="already-applied"]',
            ):
                try:
                    if await page.query_selector(sel):
                        already_applied = True
                        break
                except Exception:
                    continue

            page_title = ""
            try:
                page_title = await page.title()
            except Exception:
                pass

            return {
                "status": "error",
                "message": (
                    "Already applied — found 'View Proposal' / 'Withdraw' button"
                    if already_applied
                    else f"Apply button not found at {page.url} (title: {page_title!r}). "
                         "Job may be closed, the URL may be a proposal-edit URL, or "
                         "Upwork's selectors changed."
                ),
                "page_url": page.url,
                "already_applied": already_applied,
            }

        await apply_btn.click()
        await page.wait_for_load_state("networkidle")
        # Air3 takes ~2s to finish hydrating after networkidle —
        # without this wait, the rate-increase dropdown commits don't
        # stick because the Vue components are still mid-mount and
        # don't yet have their event handlers wired. Verified
        # 2026-05-05 — without this sleep, even the working blur
        # technique fails when run in this code path.
        import asyncio as _aio
        await _aio.sleep(2.0)

    # Handle Upwork's "Schedule a rate increase" required section FIRST,
    # before ANY other form interaction. Air3's v-model only commits to
    # the validator on focusout. Other field interactions (rate/bid/
    # cover-letter) re-trigger form-level validation in ways that
    # silently invalidate a partially-committed dropdown selection.
    # Doing this first + immediately blurring (the helper handles that)
    # locks in the value before the form has anything else to react to.
    # Verified 2026-05-05 against the live form.
    await _handle_rate_increase_section(page)

    # Fill cover letter — helper already focused the textarea as part of
    # the dropdown-blur step, so we just fill (no extra click) to avoid
    # disturbing the rate-increase commit state.
    cover_textarea = await page.query_selector('[data-test="cover-letter-input"], textarea[name*="cover"], textarea')
    if cover_textarea:
        await cover_textarea.fill(params.cover_letter)

    # Fill rate / bid AFTER rate-increase + cover letter so any
    # form re-validation triggered by these inputs sees the rate-
    # increase already committed.
    if params.rate:
        rate_input = await page.query_selector('[data-test="hourly-rate-input"], input[name*="rate"]')
        if rate_input:
            await rate_input.fill(str(params.rate))

    if params.bid:
        bid_input = await page.query_selector('[data-test="bid-input"], input[name*="bid"], input[name*="amount"]')
        if bid_input:
            await bid_input.fill(str(params.bid))

    # Answer screening questions if provided
    if params.answers:
        question_inputs = await page.query_selector_all('[data-test="question-input"], .question-answer textarea, .screening-question textarea')
        for i, answer in enumerate(params.answers):
            if i < len(question_inputs):
                await question_inputs[i].fill(answer)

    # Check for connects required
    connects_el = await page.query_selector('[data-test="connects-required"], .connects-info')
    connects_required = 0
    if connects_el:
        text = (await connects_el.text_content() or "")
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            connects_required = int(numbers[0])

    # Submit the proposal
    submit_btn = await page.query_selector('[data-test="submit-proposal"], button[type="submit"]:has-text("Submit"), button:has-text("Send")')
    if not submit_btn:
        return {"status": "error", "message": "Submit button not found"}

    pre_submit_url = page.url
    await submit_btn.click()

    # ── Two-step submit confirmation flow ────────────────────────────
    # After clicking "Send for N Connects", Upwork opens a confirmation
    # modal titled "Stay safe & build your reputation" with a required
    # "I understand Upwork's policies" checkbox + an initially-disabled
    # Submit button. Without checking + clicking through this modal
    # the proposal is never actually submitted (Upwork keeps the user
    # on /apply/ silently). Verified 2026-05-05 against the live form.
    import asyncio as _aio

    modal = page.locator('[role="dialog"][name="intro"], [role="dialog"]:has-text("Stay safe")').first
    try:
        await modal.wait_for(state="visible", timeout=8000)
        modal_present = True
    except Exception:
        modal_present = False

    if modal_present:
        # Check the policy agreement checkbox via its label (the real
        # input is .sr-only). Locator click on the label toggles the
        # underlying input through Air3's normal event flow.
        try:
            checkbox_label = modal.locator('[data-test="checkbox-label"]').first
            await checkbox_label.click()
            await _aio.sleep(0.3)
        except Exception:
            # Fallback: try the fake-input visual element + native input
            try:
                await modal.locator('[data-test="checkbox-input"]').first.click()
                await _aio.sleep(0.3)
            except Exception:
                try:
                    await modal.locator('input[type="checkbox"]').first.check()
                    await _aio.sleep(0.3)
                except Exception:
                    pass

        # Wait for the modal Submit button to become enabled.
        modal_submit = modal.locator(
            'button[data-ev-label="disintermediation_modal_accept"], '
            'button.air3-btn-primary:has-text("Submit")'
        ).first
        # Poll for enabled state up to 3s.
        for _ in range(30):
            try:
                disabled = await modal_submit.get_attribute("disabled")
                if not disabled:
                    break
            except Exception:
                break
            await _aio.sleep(0.1)

        try:
            await modal_submit.click()
        except Exception:
            return {
                "status": "error",
                "message": (
                    "Policy modal opened but its Submit button never enabled — "
                    "the policy checkbox click didn't register. Form may have "
                    "redesigned the modal."
                ),
                "page_url": page.url,
            }

    # Now poll for the actual submission outcome.
    _deadline = _aio.get_event_loop().time() + 15.0
    while _aio.get_event_loop().time() < _deadline:
        # 1. Explicit success element
        ok = await page.query_selector('[data-test="proposal-submitted"], .success-message')
        if ok:
            return {
                "status": "submitted",
                "connects_used": connects_required,
                "message": "Proposal submitted successfully",
                "page_url": page.url,
            }
        # 2. URL changed away from /apply/ → Upwork redirected, success.
        if "/apply/" not in page.url and page.url != pre_submit_url:
            return {
                "status": "submitted",
                "connects_used": connects_required,
                "message": f"Proposal submitted (redirected to {page.url})",
                "page_url": page.url,
            }
        await _aio.sleep(0.5)

    # Polling exhausted — fall through to error capture.
    try:
        # Collect ALL inline validation errors (Upwork uses red "Enter a X"
        # messages under each unfilled required field). Without this the
        # brain only sees a generic "Could not confirm" — useless for
        # diagnosing which field is blocking. Returning the joined list
        # lets the agent reason about WHAT to fix.
        # CRITICAL: filter by visibility. Air3 keeps every potential
        # error element in the DOM and toggles ``display: none``. Reading
        # ``text_content`` alone gives every possible error message,
        # producing false positives that look like "form failed" when the
        # submit actually succeeded — verified 2026-05-05 with the
        # rate-increase debugging session. Only include errors that the
        # user would actually SEE on screen.
        validation_errors = await page.evaluate(
            """
            (args) => {
              const [selectors, ignorePatterns] = args;
              const seen = new Set();
              const out = [];
              for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(el => {
                  const cs = window.getComputedStyle(el);
                  if (cs.display === 'none' || cs.visibility === 'hidden' || el.offsetParent === null) return;
                  const txt = (el.textContent || '').trim();
                  if (!txt || txt.length >= 200) return;
                  if (seen.has(txt)) return;
                  // Filter out promo / informational banners that aren't
                  // real validation errors. Verified 2026-05-05: Upwork's
                  // apply form keeps "Buy more Connects" promo + bid info
                  // permanently visible regardless of submit state.
                  const lower = txt.toLowerCase();
                  if (ignorePatterns.some(p => lower.includes(p))) return;
                  seen.add(txt);
                  out.push(txt);
                });
              }
              return out;
            }
            """,
            [
                [
                    '[data-test*="error"]',
                    '[role="alert"]',
                    '.air3-form-message-error',
                    '.air3-error-message',
                    '.alert-danger',
                ],
                # Promo / info nudges (case-insensitive substring match):
                [
                    "buy more connects",
                    "rank in 1st place",
                    "your bid is set to",
                ],
            ],
        )

        # If after visibility-filtering we have NO errors AND URL is
        # still /apply/, the submit click was either silently rejected
        # by server-side validation OR is still in flight. Don't lie
        # — return ``unknown`` so the brain treats this as "needs
        # verification" instead of celebrating.
        if not validation_errors:
            return {
                "status": "unknown",
                "message": (
                    f"Submit clicked, no visible client-side errors, but "
                    f"page is still at {page.url} (no redirect, no success "
                    f"element). Server may have rejected silently — verify "
                    f"by visiting your Upwork proposals list."
                ),
                "page_url": page.url,
            }

        return {
            "status": "error",
            "message": "Form validation failed: " + " | ".join(validation_errors[:6]),
            "validation_errors": validation_errors,
            "page_url": page.url,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "message": f"Submit-confirmation phase raised: {exc}. Page: {page.url}",
            "page_url": page.url,
        }


async def withdraw_proposal(
    proposal_url: str, reason: str = "Applied by mistake",
) -> dict:
    """Withdraw a submitted proposal via the two-step Upwork flow.

    Verified against the live form 2026-05-05 — the flow is:

    1. Navigate to ``proposal_url`` (``/nx/proposals/<id>``).
    2. Click the page-level ``Withdraw proposal`` button.
    3. A modal opens (``[role="dialog"]`` titled "Withdraw proposal")
       with a required reason radio group:
         - "Applied by mistake" (default selected)
         - "Rate too low"
         - "Scheduling conflict with client"
         - "Unresponsive client"
         - "Inappropriate client behavior"
         - "Other"
    4. Click the modal's green ``Withdraw`` button to confirm.

    Args:
        proposal_url: ``/nx/proposals/<id>`` URL to withdraw.
        reason: Reason text matching one of the radio labels exactly.
                Default ``"Applied by mistake"`` — already pre-selected,
                so we don't need to touch the radio. Pass any other
                label to pick a different reason.

    Returns:
        ``{"status": "withdrawn"|"error"|"unknown", "message": ..., "page_url": ...}``
    """
    import asyncio as _aio
    import logging
    logger = logging.getLogger(__name__)

    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto(proposal_url, wait_until="networkidle")
    if not await _wait_for_cloudflare_clear(page, 15.0):
        return {
            "status": "error",
            "message": "Cloudflare challenge blocking proposal page. Solve in Brave and retry.",
            "page_url": page.url,
        }

    # ── Step 1: open the withdraw confirmation modal ────────────────
    page_withdraw = page.locator(
        '[data-test="withdraw-button"], '
        'button:has-text("Withdraw proposal"), '
        'button:has-text("Withdraw")'
    ).first
    try:
        await page_withdraw.wait_for(state="visible", timeout=8000)
    except Exception:
        # Maybe already withdrawn / archived
        return {
            "status": "error",
            "message": (
                f"Withdraw button not found on {page.url}. The proposal "
                f"may already be withdrawn, archived, or unavailable."
            ),
            "page_url": page.url,
        }
    await page_withdraw.click()

    # ── Step 2: wait for reason modal, optionally pick a non-default reason ──
    modal = page.locator(
        '[role="dialog"]:has-text("Withdraw proposal")'
    ).first
    try:
        await modal.wait_for(state="visible", timeout=8000)
    except Exception:
        return {
            "status": "unknown",
            "message": (
                f"Clicked Withdraw on {page.url} but the confirmation "
                f"modal didn't appear. Page may have changed."
            ),
            "page_url": page.url,
        }

    # Click the reason radio — even if Upwork visually highlights one
    # by default, the modal's Submit button stays disabled until the
    # user explicitly picks a reason. The radio's label is the
    # canonical clickable element for Air3 radios (the underlying
    # ``<input>`` is ``.sr-only``).
    chosen_reason = (reason or "Applied by mistake").strip()
    try:
        radio_label = modal.locator("label", has_text=chosen_reason).first
        if await radio_label.count():
            await radio_label.click()
            await _aio.sleep(0.3)
            logger.info("withdraw_proposal: selected reason %r", chosen_reason)
        else:
            # Fallback: click the first radio in the modal so the
            # submit button enables (better than failing entirely).
            first_radio = modal.locator('input[type="radio"], [role="radio"]').first
            if await first_radio.count():
                await first_radio.click()
                await _aio.sleep(0.3)
                logger.info(
                    "withdraw_proposal: reason %r not found, clicked first radio",
                    chosen_reason,
                )
    except Exception:
        logger.debug(
            "withdraw_proposal: failed to select reason — proceeding anyway",
            exc_info=True,
        )

    # ── Step 3: click the modal's green Withdraw button ─────────────
    # Modal footer has Cancel + green Withdraw. Use the primary class
    # alone — the modal title literally says "Withdraw proposal" so any
    # text-based negative-filter against "proposal" risks matching the
    # heading instead of the button. ``air3-btn-primary`` is the
    # canonical Air3 selector for the green confirm button.
    confirm = modal.locator(
        'button.air3-btn-primary, button[data-ev-label*="withdraw"]'
    ).first
    try:
        await confirm.wait_for(state="visible", timeout=5000)
        for _ in range(30):
            disabled = await confirm.get_attribute("disabled")
            if not disabled:
                break
            await _aio.sleep(0.1)
        await confirm.click()
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Modal Withdraw button click failed: {exc}",
            "page_url": page.url,
        }

    # ── Step 4: confirm via URL change or status text ───────────────
    pre_url = page.url
    deadline = _aio.get_event_loop().time() + 12.0
    while _aio.get_event_loop().time() < deadline:
        # Modal closed?
        try:
            modal_visible = await modal.is_visible()
        except Exception:
            modal_visible = False
        if not modal_visible:
            # Re-check URL or page text to confirm.
            try:
                body_lower = await page.evaluate(
                    "() => document.body.textContent.toLowerCase()"
                )
            except Exception:
                body_lower = ""
            if "withdrawn" in body_lower or page.url != pre_url:
                return {
                    "status": "withdrawn",
                    "message": "Proposal withdrawn successfully",
                    "page_url": page.url,
                }
        await _aio.sleep(0.4)

    # Last-ditch: explicit success element
    if await page.query_selector(
        '[data-test="withdrawal-confirmed"], .success, .success-message'
    ):
        return {
            "status": "withdrawn",
            "message": "Proposal withdrawn successfully",
            "page_url": page.url,
        }

    return {
        "status": "unknown",
        "message": (
            f"Withdraw modal closed but couldn't confirm success. "
            f"Verify by visiting your Upwork proposals list. Page: {page.url}"
        ),
        "page_url": page.url,
    }


async def _wait_for_cloudflare_clear(page, max_wait_s: float = 20.0) -> bool:
    """Poll until Cloudflare's "Just a moment..." interstitial clears.

    ``page.goto(..., wait_until="networkidle")`` returns while Cloudflare's
    challenge is still rendering — the interstitial issues its own
    background fetches that satisfy networkidle, then JS-redirects to the
    real page when the challenge auto-validates. So we have to look at
    the document title.

    Returns True when the title no longer says "moment" (or contains real
    Upwork content), False if the challenge sticks past max_wait_s.
    """
    import asyncio as _aio

    deadline = _aio.get_event_loop().time() + max_wait_s
    while _aio.get_event_loop().time() < deadline:
        try:
            title = (await page.title() or "").lower()
        except Exception:
            return False
        if "moment" not in title and "checking your browser" not in title:
            return True
        await _aio.sleep(0.5)
    return False


async def _handle_rate_increase_section(page) -> None:
    """Defuse Upwork's "Schedule a rate increase" required section.

    Verified against the real Air3-design-system DOM on 2026-05-05:

    * Section container: ``[data-test="sri-input"]`` (sri = "schedule rate
      increase"). When this isn't on the page, there's nothing to do.
    * Two children of the section: ``[data-test="dropdown-toggle"]``,
      both ``role="combobox"``. NOT native ``<select>``. Order is
      ``[0] = frequency``, ``[1] = percent``.
    * Clicking a toggle opens an Air3 menu — items have classes
      ``.air3-menu-item[role="option"]``.
    * Real frequency options: ``Never``, ``Every 3/6/12 months``.
    * Real percent options: ``5%``, ``10%``, ``15%``, ``Custom``
      (no ``0%`` exists — pick lowest numeric, never ``Custom`` because
      it opens a side panel that needs additional input).

    Strategy:
        * frequency → ``Never`` (no rate increase ever — freelancer-safe).
          Picking ``Never`` may collapse the percent field; we
          re-check before touching it.
        * percent (if still required) → lowest numeric option (``5%``).
          Never picks ``Custom``.

    Best-effort: every step is wrapped in try/except. If Upwork rotates
    the DOM again, ``submit_proposal``'s ``validation_errors`` capture
    surfaces the real new error instead of pretending success.
    """
    import asyncio
    import logging
    import re as _re

    logger = logging.getLogger(__name__)

    # Use Playwright locators throughout — element_handles routed
    # through query_selector skip some actionability checks the v-model
    # commit relies on.
    section = page.locator('[data-test="sri-input"]')
    try:
        if not await section.count():
            logger.debug("Rate-increase section not present — skipping defuse")
            return
    except Exception:
        logger.debug("Rate-increase: section probe raised", exc_info=True)
        return

    toggles = section.locator('[data-test="dropdown-toggle"]')
    try:
        toggle_count = await toggles.count()
    except Exception:
        toggle_count = 0
    if toggle_count < 2:
        logger.warning(
            "Rate-increase: expected 2 dropdown-toggles inside sri-input, got %d",
            toggle_count,
        )
        return

    async def _pick_option(predicate_label: str, predicate) -> bool:
        """Find an open menu item whose text matches predicate; click it.

        Use Playwright's ``page.locator(...).click()`` API, NOT
        ``element_handle.click()``. The locator click adds:

        * Auto-scroll-into-view before the click
        * Actionability checks (visible, enabled, stable)
        * Real CDP ``Input.dispatchMouseEvent`` (``isTrusted=true``)

        ``element_handle.click()`` skips some of these and Air3's
        form-bind handler appears to ignore the resulting events even
        though the visual label updates. Verified 2026-05-05.
        """
        items = page.locator('.air3-menu-item[role="option"]')
        try:
            count = await items.count()
        except Exception:
            return False

        for i in range(count):
            item = items.nth(i)
            try:
                txt = ((await item.text_content()) or "").strip()
            except Exception:
                continue
            if predicate(txt):
                try:
                    await item.scroll_into_view_if_needed()
                    await item.click()
                    logger.info(
                        "Rate-increase: %s → picked %r", predicate_label, txt,
                    )
                    return True
                except Exception:
                    logger.debug(
                        "Rate-increase: click failed on %r", txt, exc_info=True,
                    )
        return False

    async def _wait_for_toggle_label(toggle, expected_substr: str, max_s: float = 3.0) -> bool:
        """Wait until the toggle's label text contains expected_substr.

        Air3 dropdowns visually update the label synchronously, but
        Vue's reactive v-model propagation to the underlying form value
        is async. Without this wait, the form submit click fires before
        Vue commits the change → validation sees the field as still
        empty. Polling the label is a reliable proxy for "value committed."
        """
        deadline = asyncio.get_event_loop().time() + max_s
        target = expected_substr.lower()
        while asyncio.get_event_loop().time() < deadline:
            try:
                txt = ((await toggle.text_content()) or "").strip().lower()
            except Exception:
                return False
            if target in txt:
                # One extra tick so Vue's reactive watchers run.
                await asyncio.sleep(0.15)
                return True
            await asyncio.sleep(0.1)
        return False

    # ── Frequency: pick "Never" (no increase ever) ──────────────────
    picked_label = ""
    freq_toggle_loc = toggles.nth(0)
    try:
        await freq_toggle_loc.click()
        await asyncio.sleep(0.4)  # Air3 menu animates in

        picked = await _pick_option(
            "frequency",
            lambda t: t.lower() == "never",
        )
        picked_label = "Never" if picked else ""
        if not picked:
            picked = await _pick_option(
                "frequency-fallback-12mo",
                lambda t: "12 month" in t.lower(),
            )
            picked_label = "12 month" if picked else ""
        if not picked:
            picked = await _pick_option(
                "frequency-fallback-year",
                lambda t: "year" in t.lower() or "annual" in t.lower(),
            )
            picked_label = "year" if picked else ""
        if not picked:
            picked = await _pick_option(
                "frequency-fallback-first",
                lambda t: bool(t.strip()),
            )

        # Wait for Vue's v-model to commit the choice — submit fires
        # before this completes otherwise, and the form validates as
        # if frequency was never picked. Polling the toggle label is
        # the deterministic "value committed" signal we need.
        if picked and picked_label:
            committed = await _wait_for_toggle_label(freq_toggle_loc, picked_label)
            if not committed:
                logger.warning(
                    "Rate-increase: frequency label didn't commit %r in time — "
                    "form may still validate as empty",
                    picked_label,
                )

    except Exception:
        logger.debug(
            "Rate-increase: frequency dropdown click failed",
            exc_info=True,
        )

    # ── Percent: pick lowest numeric option (skip "Custom") ──────────
    # Picking "Never" frequency may hide/disable the percent field. We
    # re-query the toggles inside the section after a short pause so we
    # don't click a stale element handle. If percent toggle is gone or
    # disabled, skip percent — but still run the blur step at the end.
    pct_skipped = False
    pct_toggle_loc = None
    try:
        await asyncio.sleep(0.3)
        toggles_after = section.locator('[data-test="dropdown-toggle"]')
        n_after = await toggles_after.count()
        if n_after < 2:
            logger.info(
                "Rate-increase: percent toggle gone after frequency pick — skipping percent",
            )
            pct_skipped = True
        else:
            pct_toggle_loc = toggles_after.nth(1)
            try:
                visible = await pct_toggle_loc.is_visible()
            except Exception:
                visible = True
            if not visible:
                logger.info("Rate-increase: percent toggle not visible — skipping")
                pct_skipped = True
    except Exception:
        logger.debug(
            "Rate-increase: percent dropdown probe failed", exc_info=True,
        )
        pct_skipped = True

    # If we have a percent toggle to pick, do it.
    if not pct_skipped and pct_toggle_loc is not None:
        try:
            await pct_toggle_loc.click()
            await asyncio.sleep(0.4)

            # Find the lowest numeric percent option, never "Custom".
            candidates = await page.query_selector_all(
                '.air3-menu-item[role="option"]'
            )
            best_pct: int | None = None
            best_opt = None
            for opt in candidates:
                try:
                    txt = ((await opt.text_content()) or "").strip()
                except Exception:
                    continue
                if txt.lower() == "custom":
                    continue
                m = _re.search(r"^(\d+)\s*%", txt)
                if m:
                    pct = int(m.group(1))
                    if best_pct is None or pct < best_pct:
                        best_pct = pct
                        best_opt = opt
            if best_opt is not None:
                try:
                    await best_opt.click()
                    logger.info(
                        "Rate-increase: percent → picked %d%%", best_pct,
                    )
                    await _wait_for_toggle_label(pct_toggle_loc, f"{best_pct}%")
                except Exception:
                    logger.debug(
                        "Rate-increase: percent option click failed", exc_info=True,
                    )
            else:
                logger.warning(
                    "Rate-increase: no numeric percent option found among %d items",
                    len(candidates),
                )
        except Exception:
            logger.debug(
                "Rate-increase: percent dropdown handling failed", exc_info=True,
            )

    # Critical commit-on-blur trick. Air3 dropdown updates its visual
    # label on option click, but the form-validator state only commits
    # when the dropdown loses focus. Clicking the cover-letter textarea
    # is the natural blur target — it's focusable, present on the
    # apply form, and the next step in the flow is to fill it anyway.
    # Without this blur the validator still reports "Enter a rate-
    # increase frequency/percent" even though the toggle visually
    # shows the picked value. Verified 2026-05-05 against the live form.
    blurred = False
    for blur_sel in (
        '[data-test="cover-letter-input"]',
        'textarea[name*="cover"]',
        'textarea',
        'input[type="text"]',
        'input[type="number"]',
    ):
        try:
            target = page.locator(blur_sel).first
            n = await target.count()
            logger.debug("Rate-increase: blur try %r → count=%d", blur_sel, n)
            if n:
                await target.click(timeout=5000)
                await asyncio.sleep(0.3)
                logger.info(
                    "Rate-increase: blurred dropdowns via %r focus", blur_sel,
                )
                blurred = True
                break
        except Exception as exc:
            logger.info("Rate-increase: blur via %r raised: %s", blur_sel, exc)
            continue
    if not blurred:
        # Last-ditch JS blur on the active element
        try:
            await page.evaluate(
                "() => { if (document.activeElement) document.activeElement.blur(); }"
            )
            logger.info("Rate-increase: fallback JS .blur() on activeElement")
        except Exception:
            logger.debug("Rate-increase: even JS blur failed", exc_info=True)
