"""Proposal tools for Upwork MCP."""

import logging

from pydantic import BaseModel, Field

from ..browser.client import get_browser

logger = logging.getLogger(__name__)


class ProposalsParams(BaseModel):
    """Parameters for getting proposals."""
    status: str = Field(
        default="all",
        description=(
            "Filter by status: 'active' (live proposals waiting for client "
            "response — usually 0 in the 2026 layout), 'submitted' (sent and "
            "still pending), 'archived' (rejected/withdrawn — not yet wired), "
            "or 'all' (active + submitted). Default 'all' so 'have I applied "
            "to this job?'-style checks see everything; the brain LLM was "
            "previously getting [NO DATA] from default-active calls because "
            "Upwork moves rows out of 'active' immediately after submission."
        ),
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")


class SubmitProposalParams(BaseModel):
    """Parameters for submitting a proposal."""
    job_url: str = Field(description="Full Upwork job URL")
    cover_letter: str = Field(description="Cover letter content")
    rate: float | None = Field(default=None, description="Proposed hourly rate (for hourly jobs)")
    bid: float | None = Field(default=None, description="Bid amount (for fixed-price jobs)")
    answers: list[str] | None = Field(
        default=None,
        description=(
            "Answers to the job's screening questions, in the same order "
            "the apply page displays them. If the job has questions and "
            "this is ``None`` (or shorter than the question count), the "
            "tool returns early with ``status='needs_answers'`` and the "
            "question text — draft answers, then re-call with this list "
            "populated. Skipping this round-trip causes Upwork to reject "
            "the submit silently with red inline 'Please answer this "
            "question' errors."
        ),
    )
    milestone_description: str | None = Field(
        default=None,
        description=(
            "Single-milestone description for fixed-price jobs. Required by "
            "Upwork's milestone-breakdown form when bid is set. ``None`` falls "
            "back to a generic 'Project completion' string. Multi-milestone "
            "splits are not yet supported — use a single milestone equal to "
            "the bid and discuss the breakdown on the discovery call."
        ),
    )
    milestone_due_date: str | None = Field(
        default=None,
        description=(
            "Due date for the single milestone, format MM/DD/YYYY. Required "
            "by Upwork's fixed-price form. ``None`` defaults to 30 days "
            "from today (the freelancer can renegotiate after acceptance)."
        ),
    )
    project_duration: str | None = Field(
        default=None,
        description=(
            "Project duration label as Upwork shows it in the 'How long "
            "will this project take?' dropdown. Options seen 2026-05-09: "
            "'Less than 1 month', '1 to 3 months', '3 to 6 months', "
            "'More than 6 months'. ``None`` defaults to 'Less than 1 month'."
        ),
    )
    connects_to_send: int | None = Field(
        default=None,
        description=(
            "Total Connects to spend on this proposal (mandatory + boost). "
            "Upwork defaults vary by job (often 6); pass a lower number to "
            "spend fewer Connects. Set to your remaining balance to avoid "
            "overspend. ``None`` keeps Upwork's auto-suggested default."
        ),
    )


async def get_proposals(params: ProposalsParams) -> list[dict]:
    """Get your submitted proposals on Upwork.

    Upwork dropped the ?status= query in the 2026 redesign — when the targeted
    sub-list is empty (e.g. ?status=active with no live proposals) Upwork now
    redirects to /nx/find-work/best-matches. We always land on the bare
    /nx/proposals/ page and select the right card by data-qa instead.

    Returns a list of proposals with job title, proposal URL, initiated date,
    profile used, and inferred status (active|submitted). Bid amounts are no
    longer rendered in the list view — fetch get_proposal_details for those.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto("https://www.upwork.com/nx/proposals/", wait_until="networkidle")

    # The 2026 layout uses [data-qa] (not data-test) on container cards and
    # [data-qa^="item"] on each row. Wait for either container to materialize.
    try:
        await page.wait_for_selector(
            '[data-qa="card-active-proposals"], [data-qa="card-submitted-proposals"]',
            timeout=10000,
        )
    except Exception:
        # No proposals at all, or the user landed on something unexpected.
        pass

    requested = params.status.lower()
    if requested == "active":
        containers = ["card-active-proposals"]
    elif requested == "submitted":
        containers = ["card-submitted-proposals"]
    elif requested == "archived":
        # Archived lives behind a tab click; not implemented in this patch.
        # Caller should treat empty result as "feature not yet wired".
        return []
    else:
        # "all" or unknown → both visible cards
        containers = ["card-active-proposals", "card-submitted-proposals"]

    proposals: list[dict] = []
    for container_qa in containers:
        row_els = await page.query_selector_all(
            f'[data-qa="{container_qa}"] [data-qa^="item"]'
        )
        status_value = "active" if container_qa == "card-active-proposals" else "submitted"
        for el in row_els:
            if len(proposals) >= params.limit:
                break
            try:
                proposal = await _extract_proposal(el)
            except Exception:
                continue
            if proposal:
                proposal["status"] = status_value
                proposals.append(proposal)
        if len(proposals) >= params.limit:
            break

    return proposals


async def _extract_proposal(el) -> dict | None:
    """Extract proposal data from a 2026-layout proposals row.

    Row structure (table row, data-qa="item0"/"item1"/...):
      td[data-cy="time-slot"]      → "Initiated <date>" + relative time
      td[data-cy="job-info"] a     → job title + href to /nx/proposals/<id>
      td[data-cy="ai-interview-slot"]
      td[data-qa="reason-slot"]
      td[data-cy="default-slot"]   → profile used (e.g. "General Profile")
      td[data-cy="proposal-views-slot"]
      td[data-cy="requirements-met-slot"]
    """
    proposal: dict = {}

    title_el = await el.query_selector('[data-cy="job-info"] a, a.up-n-link')
    if title_el:
        proposal["job_title"] = (await title_el.text_content() or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            url = href if href.startswith("http") else f"https://www.upwork.com{href}"
            proposal["proposal_url"] = url
            # Proposal ID is the last path segment of /nx/proposals/<id>
            tail = href.rstrip("/").split("/")[-1]
            if tail.isdigit():
                proposal["proposal_id"] = tail

    if not proposal.get("job_title"):
        return None

    time_slot = await el.query_selector('[data-cy="time-slot"]')
    if time_slot:
        text = (await time_slot.text_content() or "").strip()
        # Collapse the multi-line "Initiated\n<date>\n<relative>" into a single
        # line so the agent gets a clean string.
        proposal["initiated"] = " ".join(text.split())

    profile_slot = await el.query_selector('[data-cy="default-slot"]')
    if profile_slot:
        text = (await profile_slot.text_content() or "").strip()
        if text:
            proposal["profile_used"] = " ".join(text.split())

    views_slot = await el.query_selector('[data-cy="proposal-views-slot"]')
    if views_slot:
        text = (await views_slot.text_content() or "").strip()
        if text:
            proposal["views_info"] = " ".join(text.split())

    interview_slot = await el.query_selector('[data-cy="ai-interview-slot"]')
    if interview_slot:
        text = (await interview_slot.text_content() or "").strip()
        if text:
            proposal["interview_status"] = " ".join(text.split())

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

    Returns submission status and connects used (int — actual delta from
    a pre/post Connects-balance snapshot, or None when either snapshot
    couldn't read the balance page).
    """
    from .profile import get_connects_balance

    browser = get_browser()
    await browser.ensure_logged_in()

    # Pre-submit Connects balance snapshot. The selector-based extraction
    # this replaces (data-test="connects-required" near the legacy
    # line 555 site) was stale on the 2026 apply form and silently
    # shipped 0, so callers got "Connects used: 0" reports for every
    # submit. We pay ~3-5s extra here for an honest delta.
    #
    # CRITICAL ORDERING: take the balance BEFORE grabbing the submit-flow
    # page handle. get_connects_balance internally calls browser.get_page(),
    # which can rotate/reconnect the underlying CDP session. Holding a page
    # reference across this call leaves a stale handle and the subsequent
    # page.goto(job_url) silently lands on whatever tab the browser
    # auto-attaches to (typically /nx/find-work/best-matches), making the
    # apply flow short-circuit with "Apply button not found".
    # 8s hard cap. /nx/plans/connects/balance currently 404s in the 2026
    # layout and the helper falls back to /nx/find-work/ with selectors
    # that may also be stale, so an unbounded call hangs and triggers
    # the 60s outer tool-timeout — killing an otherwise-successful submit.
    import asyncio as _aio_balance
    pre_connects: int | None = None
    try:
        pre_balance = await _aio_balance.wait_for(get_connects_balance(), timeout=8.0)
        raw_pre = pre_balance.get("available")
        if isinstance(raw_pre, int):
            pre_connects = raw_pre
    except (Exception, _aio_balance.TimeoutError):
        pre_connects = None

    # NOW grab the page handle for the submit flow — fresh, not stale.
    page = await browser.get_page()

    # Navigate to job page first
    await page.goto(params.job_url, wait_until="networkidle")

    # Wait for Cloudflare "Just a moment..." interstitial to clear.
    # Upwork wraps every fresh navigation in this challenge — networkidle
    # fires while the challenge is still showing, so we need an explicit
    # poll. Cleared either by automatic JS validation (~5s on most home
    # networks) or stays blocked if the IP/UA looks suspicious.
    cf_cleared = await _wait_for_cloudflare_clear(page, max_wait_s=20.0)
    # Even after Cloudflare clears, Upwork's Vue app finishes hydrating
    # for ~1-2s. Without this wait the Apply button isn't yet in the
    # DOM and submit_proposal returns "Apply button not found" on
    # what's actually a healthy page. Verified 2026-05-05.
    import asyncio as _aio
    await _aio.sleep(2.0)
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

            if already_applied:
                return {
                    "status": "error",
                    "message": "Already applied — found 'View Proposal' / 'Withdraw' button",
                    "page_url": page.url,
                    "already_applied": True,
                }
            return {
                "status": "needs_user",
                "question": (
                    f"I'm on the job page ({page.url}, title: {page_title!r}) "
                    f"but can't find an Apply button with any of the known "
                    f"selectors. The job may be closed, the URL may already "
                    f"be a proposal-edit URL, or Upwork redesigned the page. "
                    f"Want me to open Brave so you can verify, give me a "
                    f"different URL, or skip this job?"
                ),
                "page_url": page.url,
                "already_applied": False,
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
    #
    # Strip Markdown before fill: Upwork's cover-letter textarea is plain
    # text in the 2026 layout, so **bold** and [link](url) survive the
    # round-trip as literal characters. Sanitizer is idempotent — already
    # plain text passes through unchanged.
    from ..utils.sanitize import strip_markdown_to_plaintext
    clean_cover = strip_markdown_to_plaintext(params.cover_letter or "")
    cover_textarea = await page.query_selector('[data-test="cover-letter-input"], textarea[name*="cover"], textarea')
    if cover_textarea:
        await cover_textarea.fill(clean_cover)

    # Attach Profile highlights from the user's portfolio (best-effort).
    # 2026 apply form has a "Profile highlights (NEW)" picker — without
    # this step every proposal ships with zero portfolio attachments,
    # because Upwork no longer auto-suggests them. Failures here never
    # block submit — function is wrapped in try/except internally.
    highlights_status = await _attach_profile_highlights(page)
    logger.info("Profile highlights: %s", highlights_status)

    # Fill rate / bid AFTER rate-increase + cover letter so any
    # form re-validation triggered by these inputs sees the rate-
    # increase already committed.
    #
    # Selector discovery is DYNAMIC — Upwork rotates data-test attrs
    # (we've seen ``hourly-rate-input``, ``currency-input``, plain
    # ``id="step-rate"``). Try the most-specific patterns first and
    # fall back to anything that visually identifies as a currency
    # input. The caller controls the VALUE; we control the discovery.
    if params.rate is not None:
        rate_input = None
        for sel in (
            '[data-test="hourly-rate-input"]',
            '#step-rate',
            'input#step-rate',
            'input[id*="step-rate"]',
            '[data-test="currency-input"]:not([id*="fee"]):not([id*="receive"])',
            'input[name*="hourlyRate"]',
            'input[name*="rate"]:not([id*="fee"]):not([id*="receive"])',
            'input[aria-label*="rate" i]',
        ):
            try:
                cand = await page.query_selector(sel)
                if cand:
                    rate_input = cand
                    break
            except Exception:
                continue
        if rate_input:
            try:
                # Triple-click + type clears prior content + fires focus
                await rate_input.click(click_count=3)
                await rate_input.fill(str(params.rate))
                # Blur to commit (currency inputs often format on blur)
                await page.keyboard.press("Tab")
            except Exception:
                # Last-ditch: at least try plain fill
                try:
                    await rate_input.fill(str(params.rate))
                except Exception:
                    pass

    if params.bid is not None:
        bid_input = None
        for sel in (
            '[data-test="bid-input"]',
            'input[name*="bid"]',
            'input[name*="amount"]',
            'input[aria-label*="bid" i]',
        ):
            try:
                cand = await page.query_selector(sel)
                if cand:
                    bid_input = cand
                    break
            except Exception:
                continue
        if bid_input:
            try:
                await bid_input.click(click_count=3)
                await bid_input.fill(str(params.bid))
                await page.keyboard.press("Tab")
            except Exception:
                pass

        # Fixed-price proposals also require the milestone breakdown
        # (description + amount). Default mode = single milestone equal
        # to the total bid; that's what we fill here. Without this the
        # form returns "A description is needed | Minimum of $5.00 |
        # Total price of the project must be greater than $5.00".
        # Verified 2026-05-09 against the live form DOM:
        #   [data-test="milestone-description"]  → text input
        #   [data-test="currency-input"]         → milestone amount
        # When a Boost dropdown / hourly-rate input is also on the page
        # the rate-fill block above already consumed currency-input, so
        # we narrow the milestone amount to the input still showing the
        # `$0.00` placeholder.
        ms_desc = (params.milestone_description or "Project completion").strip()
        try:
            desc_input = await page.query_selector('[data-test="milestone-description"]')
            if desc_input:
                await desc_input.click(click_count=3)
                await desc_input.fill(ms_desc)
                await page.keyboard.press("Tab")
        except Exception:
            pass
        try:
            currency_inputs = await page.query_selector_all('[data-test="currency-input"]')
            for cand in currency_inputs:
                try:
                    val = (await cand.input_value()) or ""
                except Exception:
                    val = ""
                # The milestone-amount field is the one still at $0.00
                # (rate / fee / receive fields are pre-filled or read-only)
                if val.strip() in ("", "$0.00", "$0", "0", "0.00"):
                    await cand.click(click_count=3)
                    await cand.fill(str(params.bid))
                    await page.keyboard.press("Tab")
                    break
        except Exception:
            pass

        # Milestone due date — Upwork renders an Air3 date-picker input
        # (inputmode="none", typed entry blocked). The DOM signature on
        # the apply form is the only `[data-test="input"]` text input
        # in a wrapper labeled "Due date for the milestone". Pattern:
        #   1. Click the input → popover calendar opens
        #   2. Use the calendar's MM/YYYY navigation + day-cell click
        # Falls back to an empty no-op when the picker isn't found so
        # hourly proposals (no milestone) keep working unchanged.
        from datetime import datetime, timedelta
        if params.milestone_due_date:
            target_str = params.milestone_due_date.strip()
            try:
                target_dt = datetime.strptime(target_str, "%m/%d/%Y")
            except ValueError:
                target_dt = datetime.now() + timedelta(days=30)
        else:
            target_dt = datetime.now() + timedelta(days=30)
        try:
            await _fill_milestone_due_date(page, target_dt)
        except Exception:
            pass

        # Project duration dropdown — required on fixed-price submits.
        # Toggle is `[data-test="dropdown-toggle"]` with
        # `aria-labelledby="duration-label"`; menu items appear in
        # `.air3-menu-item[role="option"]` once expanded.
        duration_label = (params.project_duration or "Less than 1 month").strip()
        try:
            await _select_dropdown_by_label(
                page,
                toggle_selector='[data-test="dropdown-toggle"][aria-labelledby="duration-label"]',
                option_text=duration_label,
            )
        except Exception:
            pass

    # Optional: lower the boost-connects total before submit. Upwork
    # auto-suggests a connect-spend total (mandatory + boost) on every
    # proposal — useful for ranking but expensive when you're tight on
    # connects. ``connects_to_send`` lets the caller cap the total to
    # whatever balance allows, dynamically: pass the user's remaining
    # balance and we lower the input to match.
    if params.connects_to_send is not None and params.connects_to_send > 0:
        connects_input = None
        for sel in (
            'input[type="number"][placeholder*="Connects" i]',
            'input[type="number"][aria-label*="connects" i]',
            'input[type="number"][name*="connect" i]',
            '[data-test*="connects-input"] input',
            '[data-test*="bid-connects"] input',
        ):
            try:
                cand = await page.query_selector(sel)
                if cand:
                    connects_input = cand
                    break
            except Exception:
                continue
        if connects_input:
            try:
                await connects_input.click(click_count=3)
                await connects_input.fill(str(int(params.connects_to_send)))
                await page.keyboard.press("Tab")
            except Exception:
                pass

    # Pre-submit: surface screening questions to the brain so it can draft
    # answers BEFORE the submit click. Without this, submit fires with empty
    # textareas, Upwork rejects with generic "Please answer this question"
    # inline errors, and the brain has no clean way to know what was asked
    # — it retries with the same empty params forever (verified 2026-05-11
    # on a Cyprus Claude/Codex job: 5 screening questions, every retry
    # burned ~60s and landed back on the same "Value is required" error).
    #
    # Scoping: the 2026 Upwork apply form uses Air3's ``air3-textarea
    # inner-textarea`` for every textarea, including the cover letter
    # which the brain has already populated. So we anchor on the
    # "Additional details" heading and collect textareas that fall
    # between it and the "Attachments" heading — that's exactly the
    # screening-question block. No ``data-test`` / ``aria-label`` /
    # ``placeholder`` exists on the textareas themselves, so the label
    # comes from walking up to the nearest ``<label>`` / ``<legend>`` /
    # heading / paragraph (verified 2026-05-11).
    question_texts: list[str] = await page.evaluate(
        """
        () => {
          const headings = [...document.querySelectorAll(
            'h2, h3, h4, h5, label, legend, div, span'
          )];
          const lc = s => (s || '').trim().toLowerCase();
          const startAnchor = headings.find(
            el => lc(el.textContent).startsWith('additional details')
          );
          if (!startAnchor) return [];
          const endAnchor = headings.find(
            el => lc(el.textContent).startsWith('attachments')
              || lc(el.textContent).startsWith('profile highlights')
          );

          const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;
          const textareas = [...document.querySelectorAll('textarea')].filter(ta => {
            const cs = getComputedStyle(ta);
            if (cs.display === 'none' || cs.visibility === 'hidden' || ta.offsetParent === null) return false;
            // Must come AFTER "Additional details"…
            if (!(startAnchor.compareDocumentPosition(ta) & FOLLOWS)) return false;
            // …and BEFORE "Attachments"/"Profile highlights" if those exist.
            if (endAnchor && !(ta.compareDocumentPosition(endAnchor) & FOLLOWS)) return false;
            return true;
          });

          const out = [];
          for (const ta of textareas) {
            let label = '';
            let p = ta;
            for (let i = 0; i < 7 && p && !label; i++) {
              p = p.parentElement;
              if (!p) break;
              const cand = p.querySelector(
                'label, legend, [class*="question"], [class*="label"], h4, h5, p strong, p b, p'
              );
              if (cand && !cand.contains(ta)) {
                const txt = (cand.textContent || '').trim();
                if (txt && txt.length > 5 && txt.length < 500) { label = txt; break; }
              }
            }
            if (!label) {
              label = ta.getAttribute('aria-label')
                   || ta.getAttribute('placeholder')
                   || 'Untitled screening question';
            }
            // The Cover Letter textarea lives inside the same "Additional
            // details" section on the 2026 form. Skip it by semantic label
            // (stable: Upwork's section name has been "Cover Letter" for
            // years) so we don't surface it as a fake question.
            if (lc(label) === 'cover letter') continue;
            out.push(label);
          }
          return out;
        }
        """
    )

    if question_texts and (
        not params.answers or len(params.answers) < len(question_texts)
    ):
        return {
            "status": "needs_answers",
            "questions": question_texts,
            "message": (
                f"This job has {len(question_texts)} screening question(s). "
                "Draft an answer for each (same order as listed), then re-call "
                "``submit_proposal`` with ``answers=[...]`` matching that order. "
                "Cover-letter and rate from this call are already on the page — "
                "no need to re-supply them, but pass the same values to be safe."
            ),
            "page_url": page.url,
        }

    # Answer screening questions if provided. Use the same "between
    # Additional details and Attachments" scoping as the detection block
    # above so we never overwrite the cover-letter textarea (which shares
    # the ``air3-textarea inner-textarea`` class on the 2026 form).
    if params.answers:
        question_handles = await page.evaluate_handle(
            """
            () => {
              const headings = [...document.querySelectorAll(
                'h2, h3, h4, h5, label, legend, div, span'
              )];
              const lc = s => (s || '').trim().toLowerCase();
              const startAnchor = headings.find(
                el => lc(el.textContent).startsWith('additional details')
              );
              if (!startAnchor) return [];
              const endAnchor = headings.find(
                el => lc(el.textContent).startsWith('attachments')
                  || lc(el.textContent).startsWith('profile highlights')
              );
              const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;
              // Resolve a textarea's nearest label so we can skip
              // "Cover Letter" — same heuristic as the detection block.
              const labelOf = ta => {
                let p = ta;
                for (let i = 0; i < 7; i++) {
                  p = p.parentElement;
                  if (!p) break;
                  const cand = p.querySelector(
                    'label, legend, [class*="question"], [class*="label"], h4, h5, p strong, p b, p'
                  );
                  if (cand && !cand.contains(ta)) {
                    const txt = (cand.textContent || '').trim();
                    if (txt && txt.length > 5 && txt.length < 500) return txt;
                  }
                }
                return '';
              };
              return [...document.querySelectorAll('textarea')].filter(ta => {
                const cs = getComputedStyle(ta);
                if (cs.display === 'none' || cs.visibility === 'hidden' || ta.offsetParent === null) return false;
                if (!(startAnchor.compareDocumentPosition(ta) & FOLLOWS)) return false;
                if (endAnchor && !(ta.compareDocumentPosition(endAnchor) & FOLLOWS)) return false;
                if (lc(labelOf(ta)) === 'cover letter') return false;
                return true;
              });
            }
            """
        )
        props = await question_handles.get_properties()
        question_inputs = [
            await prop.as_element() for prop in props.values()
        ]
        question_inputs = [el for el in question_inputs if el is not None]
        for i, answer in enumerate(params.answers):
            if i < len(question_inputs):
                await question_inputs[i].fill(answer)

    # NB: the legacy `[data-test="connects-required"]` extraction that
    # used to live here was stale on the 2026 layout and always shipped 0.
    # Truthful Connects-used now comes from the pre/post balance diff
    # captured at the top of the function and finalized after success.

    # Submit the proposal. Selector list is deliberately fat — verified
    # 2026-05-09 the live "Submit proposal" button has NO data-test attr
    # and is ``type="button"`` (not ``type="submit"``), so each of the
    # three legacy selectors silently misses. Match by visible text and
    # walk the fallbacks in order so older flows ("Send for N Connects")
    # still work.
    submit_btn = None
    for sel in (
        '[data-test="submit-proposal"]',
        'button:has-text("Submit proposal")',
        'button:has-text("Submit a Proposal")',
        'button:has-text("Send for")',
        'button[type="submit"]:has-text("Submit")',
        'button:has-text("Send")',
    ):
        try:
            submit_btn = await page.query_selector(sel)
            if submit_btn:
                break
        except Exception:
            continue
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
                "status": "needs_user",
                "question": (
                    "Policy modal opened (Stay safe & build your reputation) "
                    "but its Submit button never enabled — the agreement "
                    "checkbox click didn't register. Upwork may have redesigned "
                    "the modal. Want me to open Brave so you can tick the "
                    "checkbox manually, or skip this submit?"
                ),
                "page_url": page.url,
            }

    # Now poll for the actual submission outcome.
    _deadline = _aio.get_event_loop().time() + 15.0
    success_message: str | None = None
    while _aio.get_event_loop().time() < _deadline:
        # 1. Explicit success element
        ok = await page.query_selector('[data-test="proposal-submitted"], .success-message')
        if ok:
            success_message = "Proposal submitted successfully"
            break
        # 2. URL changed away from /apply/ → Upwork redirected, success.
        if "/apply/" not in page.url and page.url != pre_submit_url:
            success_message = f"Proposal submitted (redirected to {page.url})"
            break
        await _aio.sleep(0.5)

    if success_message:
        # Capture the success URL BEFORE the post-balance fetch — same
        # stale-handle reason as the pre-submit ordering above. Once
        # get_connects_balance navigates to /nx/plans/connects/balance,
        # this `page` handle may rotate and `page.url` would read the
        # balance page (or raise on a closed handle).
        success_page_url = page.url

        # Post-submit balance snapshot, computed once outside the poll
        # loop so both success branches share the same logic. Same 8s
        # hard cap as the pre-submit fetch — we never block a confirmed
        # submit on a flaky balance read.
        import asyncio as _aio_post_balance
        post_connects: int | None = None
        if pre_connects is not None:
            try:
                post_balance = await _aio_post_balance.wait_for(get_connects_balance(), timeout=8.0)
                raw_post = post_balance.get("available")
                if isinstance(raw_post, int):
                    post_connects = raw_post
            except (Exception, _aio_post_balance.TimeoutError):
                post_connects = None
        connects_used = (
            pre_connects - post_connects
            if pre_connects is not None and post_connects is not None
            else None
        )
        return {
            "status": "submitted",
            "connects_used": connects_used,  # int or None — never a fabricated 0
            "message": success_message,
            "page_url": success_page_url,
        }

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
        # by server-side validation OR is still in flight. Return
        # ``needs_user`` so the brain pauses and asks the user to verify
        # / decide instead of guessing or pretending success.
        if not validation_errors:
            try:
                cur_balance = await page.evaluate(
                    "() => { const m = document.body.textContent.match(/Remaining balance:\\s*(\\d+)/i); return m ? parseInt(m[1]) : null; }"
                )
            except Exception:
                cur_balance = None
            return {
                "status": "needs_user",
                "question": (
                    f"Submit clicked but the page didn't navigate. The form "
                    f"shows no visible validation errors, so Upwork likely "
                    f"rejected silently — common causes: insufficient "
                    f"Connects (your remaining balance: {cur_balance}), "
                    f"a server-side rule, or a duplicate-submit guard. "
                    f"Verify by checking your Upwork proposals list. Want "
                    f"me to lower ``connects_to_send`` and retry, or open "
                    f"Brave so you can see the form yourself?"
                ),
                "page_url": page.url,
                "remaining_connects": cur_balance,
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


async def _select_dropdown_by_label(page, toggle_selector: str, option_text: str) -> None:
    """Open an Air3 dropdown and click the option whose text contains
    ``option_text``. Best-effort — silently no-ops on any failure.

    Used for the project-duration dropdown on fixed-price submits where
    the value is required but the field has no native ``<select>``.
    """
    import asyncio as _aio

    toggle = page.locator(toggle_selector).first
    try:
        await toggle.wait_for(state="visible", timeout=4000)
        await toggle.click()
    except Exception:
        return
    await _aio.sleep(0.4)

    # Air3 menu items: `.air3-menu-item[role="option"]` containing the
    # option label as plain text (sometimes wrapped in nested <span>s).
    option = page.locator(
        f'.air3-menu-item[role="option"]:has-text("{option_text}"),'
        f'[role="option"]:has-text("{option_text}")'
    ).first
    try:
        await option.click(timeout=2500)
        await _aio.sleep(0.3)
    except Exception:
        return


async def _fill_milestone_due_date(page, target) -> None:
    """Drive Upwork's Air3 date picker for the milestone due-date field.

    The date input has ``inputmode="none"`` so typed entry is blocked —
    we have to open the popover calendar and click a day cell.

    Verified DOM (2026-05-09):
      - Input: ``input[data-test="input"][inputmode="none"]``
        sitting in a wrapper labelled "Due date for the milestone".
      - Day cells: ``button.air3-datepicker-table-btn`` with each
        button containing a ``<span class="sr-only">`` carrying the
        full English date e.g. ``"Saturday, June 6, 2026"``.
      - Month nav: anchor uses ``data-ev-label="datepicker_*_month"``
        but defensive selectors below also try aria-label hints.

    Strategy: open popover, look for a day-button whose sr-only text
    contains the target date string (locale-stable English output).
    If the target month isn't visible, click the next-month nav and
    retry up to 6 hops. Best-effort — silently no-ops on failure.
    """
    import asyncio as _aio

    # Upwork zero-pads day-of-month in the sr-only label
    # ("Saturday, June 06, 2026" — verified 2026-05-09). Use %d not %-d.
    target_text = target.strftime("%B %d, %Y")  # e.g. "June 06, 2026"

    locator = page.locator('input[data-test="input"][inputmode="none"]').first
    try:
        await locator.wait_for(state="visible", timeout=4000)
    except Exception:
        return
    try:
        await locator.click()
    except Exception:
        return
    await _aio.sleep(0.5)

    # Walk up to 6 months forward looking for a day button whose
    # sr-only label contains the target date string.
    for hop in range(6):
        day_btn = page.locator(
            f'button.air3-datepicker-table-btn:has(span.sr-only:has-text("{target_text}"))'
        ).first
        try:
            if await day_btn.count() > 0:
                await day_btn.click(timeout=2000)
                await _aio.sleep(0.4)
                return
        except Exception:
            pass
        # Advance one month — Upwork's nav uses data-ev-label
        next_btn = page.locator('button[data-ev-label="datepicker_next_month"]').first
        try:
            await next_btn.click(timeout=1500)
            await _aio.sleep(0.4)
        except Exception:
            return


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


async def _attach_profile_highlights(page, max_items: int = 4) -> str:
    """Open the apply form's Profile-highlights modal, select up to four
    of the user's portfolio items, and click "Add to highlights" to
    confirm.

    Verified against the live 2026 apply form on 2026-05-10:
      - Open trigger: clicking [data-test="highlights-selector"] [data-test="portfolio"]
        opens a [role=dialog] with three tabs (Upwork jobs / Portfolio /
        Certificates). Clicking the [data-test="portfolio"] card lands
        with the Portfolio tab pre-selected.
      - Per-item Add: button.item-add[data-ev-label="profile_highlights_editor_btn_add"]
        toggles each portfolio item; text changes from "Select highlight"
        to "Selected".
      - Confirm:    button.air3-btn-primary[data-ev-label="profile_highlights_editor_btn_add"]
        commits the selection and closes the dialog. Both desktop ("Add
        to highlights") and mobile ("Add N/4") variants share these
        attrs — pick the visible one.

    Returns a short status string ("attached_2", "no_items_to_attach",
    "no_highlights_section", "modal_did_not_open", "highlights_error_<X>")
    for the caller to log. Never raises — failures here must not block
    submit_proposal.
    """
    try:
        cta = await page.query_selector(
            '[data-test="highlights-selector"] [data-test="portfolio"]'
        )
        if cta is None:
            return "no_highlights_section"

        await cta.click()

        try:
            await page.wait_for_selector(
                '[role="dialog"] button[data-ev-label="profile_highlights_editor_btn_add"]',
                timeout=4000,
            )
        except Exception:
            return "modal_did_not_open"

        items = await page.query_selector_all(
            '[role="dialog"] button.item-add[data-ev-label="profile_highlights_editor_btn_add"]'
        )

        clicked = 0
        for item in items[:max_items]:
            try:
                text = (await item.text_content() or "").strip().lower()
                # Already-selected items show "Selected" — don't toggle
                # them off by clicking again.
                if text == "selected":
                    clicked += 1
                    continue
                await item.click()
                clicked += 1
            except Exception:
                continue

        if clicked == 0:
            # No items at all — close the modal cleanly so the rest of
            # the submit flow has a stable form.
            cancel = await page.query_selector(
                '[role="dialog"] button[data-ev-label="profile_highlights_editor_btn_cancel"]'
            )
            if cancel:
                try:
                    await cancel.click()
                except Exception:
                    pass
            return "no_items_to_attach"

        # Pick the visible, enabled "Add to highlights" primary button.
        # Both desktop and mobile counter variants share data-ev-label
        # and class "air3-btn-primary"; only one is actually rendered.
        confirm_candidates = await page.query_selector_all(
            '[role="dialog"] button.air3-btn-primary[data-ev-label="profile_highlights_editor_btn_add"]'
        )
        confirm = None
        for cand in confirm_candidates:
            try:
                disabled = await cand.get_attribute("disabled")
                if disabled is not None:
                    continue
                # offsetParent is null for hidden mobile/desktop variants
                visible = await cand.evaluate("e => e.offsetParent !== null")
                if visible:
                    confirm = cand
                    break
            except Exception:
                continue
        # Last-ditch fallback: any non-disabled candidate
        if confirm is None:
            for cand in confirm_candidates:
                try:
                    if await cand.get_attribute("disabled") is None:
                        confirm = cand
                        break
                except Exception:
                    continue

        if confirm is None:
            return "confirm_button_not_found"

        await confirm.click()

        # Wait for dialog to detach so the rest of the flow runs against
        # a stable form layout.
        try:
            await page.wait_for_selector('[role="dialog"]', state="detached", timeout=4000)
        except Exception:
            # Even if detach detection misses, the click already fired.
            pass

        return f"attached_{clicked}"
    except Exception as exc:
        return f"highlights_error_{type(exc).__name__}"


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
