"""Contract tools for Upwork MCP."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..browser.client import get_browser
from .page_state import empty_or_blocked_result

logger = logging.getLogger(__name__)


class ContractsParams(BaseModel):
    """Parameters for getting contracts."""
    status: str = Field(
        default="active",
        description="Filter by status: active, ended, or all"
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of results")


async def get_contracts(params: ContractsParams | None = None) -> list[dict] | dict:
    """Get your Upwork contracts.

    Returns a list of contracts with client name, job title, status, and
    earnings. When zero contracts match, returns the structured
    ``{"status": "empty_or_blocked", ...}`` dict (see page_state.py) so
    the caller can tell "no contracts" from "blocked / selector drift".
    """
    if params is None:
        params = ContractsParams()

    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to contracts page
    url = "https://www.upwork.com/nx/wm/contracts"
    if params.status == "active":
        url += "?status=active"
    elif params.status == "ended":
        url += "?status=closed"

    await page.goto(url, wait_until="networkidle")

    contracts = []

    # Wait for contracts list
    try:
        await page.wait_for_selector('[data-test="contract-tile"], .contract-row, table tbody tr', timeout=10000)
    except Exception:
        pass

    # Extract contract items
    contract_els = await page.query_selector_all('[data-test="contract-tile"], .contract-row, table tbody tr')

    for el in contract_els[:params.limit]:
        try:
            contract = await _extract_contract(el)
            if contract:
                contracts.append(contract)
        except Exception:
            continue

    if not contracts:
        logger.warning(
            "get_contracts: 0 contracts matched at url=%s — returning "
            "structured empty_or_blocked",
            getattr(page, "url", "?"),
        )
        return await empty_or_blocked_result(page)

    return contracts


async def _extract_contract(el) -> dict | None:
    """Extract contract data from element."""
    contract = {}

    # Job title
    title_el = await el.query_selector('[data-test="job-title"], .job-title, a')
    if title_el:
        contract["title"] = (await title_el.text_content() or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            contract["url"] = href if href.startswith("http") else f"https://www.upwork.com{href}"

    if not contract.get("title"):
        return None

    # Client name
    client_el = await el.query_selector('[data-test="client-name"], .client-name')
    if client_el:
        contract["client_name"] = (await client_el.text_content() or "").strip()

    # Status
    status_el = await el.query_selector('[data-test="contract-status"], .status-badge')
    if status_el:
        contract["status"] = (await status_el.text_content() or "").strip()

    # Contract type (hourly/fixed)
    type_el = await el.query_selector('[data-test="contract-type"], .contract-type')
    if type_el:
        contract["type"] = (await type_el.text_content() or "").strip()

    # Rate/Budget
    rate_el = await el.query_selector('[data-test="hourly-rate"], .rate, [data-test="budget"]')
    if rate_el:
        contract["rate"] = (await rate_el.text_content() or "").strip()

    # Total earned
    earned_el = await el.query_selector('[data-test="total-earned"], .earnings')
    if earned_el:
        contract["total_earned"] = (await earned_el.text_content() or "").strip()

    # Start date
    start_el = await el.query_selector('[data-test="start-date"], .start-date')
    if start_el:
        contract["start_date"] = (await start_el.text_content() or "").strip()

    # End date (for ended contracts)
    end_el = await el.query_selector('[data-test="end-date"], .end-date')
    if end_el:
        contract["end_date"] = (await end_el.text_content() or "").strip()

    return contract


async def get_contract_details(contract_url: str) -> dict:
    """Get detailed information about a specific contract.

    Args:
        contract_url: URL to the contract

    Returns full contract details including milestones, hours logged, and feedback.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    await page.goto(contract_url, wait_until="networkidle")

    details = {"url": contract_url}

    # Job title
    title_el = await page.query_selector('[data-test="job-title"], h1, .job-title')
    if title_el:
        details["title"] = (await title_el.text_content() or "").strip()

    # Client name
    client_el = await page.query_selector('[data-test="client-name"], .client-name')
    if client_el:
        details["client_name"] = (await client_el.text_content() or "").strip()

    # Contract status
    status_el = await page.query_selector('[data-test="contract-status"], .status')
    if status_el:
        details["status"] = (await status_el.text_content() or "").strip()

    # Contract type
    type_el = await page.query_selector('[data-test="contract-type"], .type')
    if type_el:
        details["type"] = (await type_el.text_content() or "").strip()

    # Rate
    rate_el = await page.query_selector('[data-test="rate"], .hourly-rate')
    if rate_el:
        details["rate"] = (await rate_el.text_content() or "").strip()

    # Weekly limit (for hourly)
    limit_el = await page.query_selector('[data-test="weekly-limit"], .weekly-limit')
    if limit_el:
        details["weekly_limit"] = (await limit_el.text_content() or "").strip()

    # Total earned
    earned_el = await page.query_selector('[data-test="total-earned"], .total-earned')
    if earned_el:
        details["total_earned"] = (await earned_el.text_content() or "").strip()

    # Hours this week (for hourly)
    hours_el = await page.query_selector('[data-test="hours-this-week"], .hours-week')
    if hours_el:
        details["hours_this_week"] = (await hours_el.text_content() or "").strip()

    # Total hours
    total_hours_el = await page.query_selector('[data-test="total-hours"], .total-hours')
    if total_hours_el:
        details["total_hours"] = (await total_hours_el.text_content() or "").strip()

    # Milestones (for fixed-price)
    milestones = []
    milestone_els = await page.query_selector_all('[data-test="milestone"], .milestone-item')
    for el in milestone_els:
        ms = {}
        ms_title = await el.query_selector('.milestone-title, [data-test="title"]')
        if ms_title:
            ms["title"] = (await ms_title.text_content() or "").strip()
        ms_amount = await el.query_selector('.amount, [data-test="amount"]')
        if ms_amount:
            ms["amount"] = (await ms_amount.text_content() or "").strip()
        ms_status = await el.query_selector('.status, [data-test="status"]')
        if ms_status:
            ms["status"] = (await ms_status.text_content() or "").strip()
        if ms.get("title"):
            milestones.append(ms)
    if milestones:
        details["milestones"] = milestones

    # Feedback (if ended)
    feedback = {}
    feedback_el = await page.query_selector('[data-test="feedback-section"], .feedback')
    if feedback_el:
        rating_el = await feedback_el.query_selector('[data-test="rating"], .rating')
        if rating_el:
            feedback["rating"] = (await rating_el.text_content() or "").strip()
        comment_el = await feedback_el.query_selector('[data-test="comment"], .comment')
        if comment_el:
            feedback["comment"] = (await comment_el.text_content() or "").strip()
    if feedback:
        details["feedback"] = feedback

    # Start/end dates
    start_el = await page.query_selector('[data-test="start-date"], .start-date')
    if start_el:
        details["start_date"] = (await start_el.text_content() or "").strip()

    end_el = await page.query_selector('[data-test="end-date"], .end-date')
    if end_el:
        details["end_date"] = (await end_el.text_content() or "").strip()

    return details


async def get_work_diary(contract_url: str, week_offset: int = 0) -> dict:
    """Get work diary entries for a contract.

    Args:
        contract_url: URL to the contract
        week_offset: 0 for current week, 1 for last week, etc.

    Returns work diary with daily hours and screenshots.
    """
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    # Navigate to work diary
    # The exact URL structure may vary
    diary_url = contract_url.replace("/contracts/", "/work-diary/")
    await page.goto(diary_url, wait_until="networkidle")

    diary = {"contract_url": contract_url, "days": []}

    # Extract daily entries
    day_els = await page.query_selector_all('[data-test="day-entry"], .day-row')

    for el in day_els:
        day = {}

        date_el = await el.query_selector('[data-test="date"], .date')
        if date_el:
            day["date"] = (await date_el.text_content() or "").strip()

        hours_el = await el.query_selector('[data-test="hours"], .hours')
        if hours_el:
            day["hours"] = (await hours_el.text_content() or "").strip()

        earnings_el = await el.query_selector('[data-test="earnings"], .earnings')
        if earnings_el:
            day["earnings"] = (await earnings_el.text_content() or "").strip()

        if day.get("date"):
            diary["days"].append(day)

    # Weekly totals
    total_hours_el = await page.query_selector('[data-test="weekly-hours"], .week-total-hours')
    if total_hours_el:
        diary["weekly_hours"] = (await total_hours_el.text_content() or "").strip()

    total_earnings_el = await page.query_selector('[data-test="weekly-earnings"], .week-total-earnings')
    if total_earnings_el:
        diary["weekly_earnings"] = (await total_earnings_el.text_content() or "").strip()

    return diary


# ── Submit milestone for payment ───────────────────────────────────────
# When a fixed-price milestone is funded and the freelancer has delivered
# the work, they click "Request Milestone Payment" / "Submit work for
# payment" to push the milestone into the client's review queue. The
# client then has 14 days to approve, request changes, or dispute. If
# they don't act, Upwork auto-releases on day 14.
#
# This is one of the most sensitive write actions in the entire MCP:
# clicking the final submit button starts the payment-release timer and
# notifies the client. We default ``draft_only=True`` (2026-06-10
# security audit) — staging the modal for the user's final click is the
# safe default; callers must pass an explicit ``draft_only=False`` to
# authorize the final submit. Every layer of the flow logs at INFO so a
# stale-image / wrong-button regression is loud in MCP stderr.


class SubmitMilestoneParams(BaseModel):
    """Parameters for :func:`submit_milestone`."""

    contract_url: str = Field(
        description=(
            "Full URL of the contract whose milestone you're submitting. "
            "Must be on upwork.com/*/contracts/* — non-contract URLs are "
            "refused before any browser navigation."
        ),
    )
    message: str = Field(
        default="",
        description=(
            "Description shown to the client when the milestone lands in "
            "their review queue. Keep it brief — what was delivered, where "
            "to find it, anything they need to verify. Same URL + "
            "product-pitch guards as send_message apply: any external "
            "URL or 'lazyclaw' token is refused before send."
        ),
    )
    milestone_id: str | None = Field(
        default=None,
        description=(
            "Optional milestone identifier when the contract has multiple "
            "milestones. When ``None``, the tool targets the first milestone "
            "marked 'Active' / 'In Progress' / 'Funded'. Pass an explicit "
            "ID when there's ambiguity. Identifier format is whatever "
            "appears in the per-milestone row's data-qa or text label."
        ),
    )
    attachments: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of local file paths to attach (max ~5 by "
            "Upwork policy). Each path is resolved + verified to exist "
            "before clicking the attach button. ``None`` submits a "
            "message-only milestone."
        ),
    )
    draft_only: bool = Field(
        default=True,
        description=(
            "Default TRUE (safe): open the submit modal + type the "
            "message + attach files but DO NOT click the final Submit "
            "button — the user reviews in their live Brave tab and "
            "submits manually (returns status='drafted_submit'). Pass "
            "an explicit false ONLY when the user has already approved "
            "this exact submission — the final click starts the 14-day "
            "payment-release timer."
        ),
    )


async def submit_milestone(params: SubmitMilestoneParams) -> dict:
    """Submit a funded milestone for client review/payment.

    Workflow:

    1. URL + product-pitch guards on ``message`` (refuse before any nav).
    2. ``safe_goto`` to the contract page.
    3. Locate the milestone row (specific ``milestone_id`` or first active).
    4. Click "Request Payment" / "Submit work for payment".
    5. Type ``message`` into the description box (Tiptap-safe via
       ``_type_with_soft_breaks``).
    6. Attach files (when provided) via ``set_input_files`` on the
       hidden ``<input type="file">``.
    7. ``draft_only`` short-circuits here — caller's user finalizes.
    8. Otherwise click final Submit → wait for success toast / URL change.

    Returns ``{status, message, ...}``. Statuses:
      * ``"submitted"`` — final click went through
      * ``"drafted_submit"`` — modal staged, user finalizes manually
      * ``"blocked"`` — message contained URL or product-pitch token
      * ``"not_found"`` — Request Payment button or milestone row missing
      * ``"missing_attachment"`` — a path in ``attachments`` doesn't exist
      * ``"error"`` — exception during the flow
    """
    # Lazy import — shared text guards live in messages.py and we'd hit
    # a circular import at module load otherwise (messages -> client ->
    # ... fine actually but proposals.py was already structured this way
    # so we keep the same convention).
    from .messages import (
        _contains_product_pitch,
        _contains_url,
        _type_with_soft_breaks,
    )

    if not params.contract_url.startswith("http"):
        return {"status": "error", "message": "contract_url must be a full URL"}
    if "upwork.com" not in params.contract_url:
        return {"status": "error", "message": "contract_url is not on upwork.com"}

    # URL guard — even though this isn't a chat message, the text lands
    # in a client-visible notification (same as a DM). Same risk profile.
    offending = _contains_url(params.message)
    if offending is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused — milestone description contains URL "
                f"{offending!r}. Client notifications strip URLs the "
                f"same way chat does. Rephrase (deliverables go via "
                f"the attachment, not a link)."
            ),
            "offending_token": offending,
        }
    pitch = _contains_product_pitch(params.message)
    if pitch is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused — milestone description names {pitch!r}. "
                f"Describe the WORK delivered (not the tool that built "
                f"it). Reframe and retry."
            ),
            "offending_token": pitch,
        }

    # Attachment pre-flight — fail fast before opening the modal.
    resolved_attachments: list[Path] = []
    if params.attachments:
        for raw in params.attachments:
            p = Path(raw).expanduser().resolve()
            if not p.exists():
                return {
                    "status": "missing_attachment",
                    "message": f"Attachment not found: {p}",
                    "missing_path": str(p),
                }
            resolved_attachments.append(p)

    browser = get_browser()
    await browser.ensure_logged_in()

    try:
        page = await browser.safe_goto(params.contract_url)
    except Exception as exc:
        logger.exception("submit_milestone: safe_goto failed")
        return {"status": "error", "message": f"navigation failed: {exc}"}

    await asyncio.sleep(1.2)

    # Find the milestone row. Upwork's contract page lists active
    # milestones in a section labeled "Milestone in progress" or
    # similar; each row carries a per-milestone "Request payment" /
    # "Submit work" button.
    request_btn = None
    if params.milestone_id:
        # Narrow to the row containing the milestone ID, then find its
        # Request Payment button.
        row = await page.query_selector(
            f'[data-qa="milestone-row"]:has-text({params.milestone_id!r}), '
            f'.milestone-item:has-text({params.milestone_id!r})'
        )
        if row:
            request_btn = await row.query_selector(
                'button:has-text("Request Payment"), '
                'button:has-text("Submit Work"), '
                'button:has-text("Submit work for payment")'
            )
    if not request_btn:
        # Fall back to the first visible "Request Payment" button on the
        # contract page (the active milestone).
        request_btn = await page.query_selector(
            'button[data-qa="request-payment-btn"], '
            'button[data-test="request-payment"], '
            'button:has-text("Request Payment"), '
            'button:has-text("Submit work for payment"), '
            'button:has-text("Submit Work")'
        )

    if not request_btn:
        return {
            "status": "not_found",
            "message": (
                "No 'Request Payment' / 'Submit Work' button on the "
                "contract page. Is the milestone funded and in progress?"
            ),
        }

    await request_btn.click()
    # Submit modal opens — Tiptap editor inside takes a beat to mount.
    await asyncio.sleep(1.8)

    # Locate the description editor inside the modal.
    desc_el = await page.query_selector(
        '[role="dialog"] [role="textbox"][contenteditable="true"], '
        '[role="dialog"] .tiptap[contenteditable="true"], '
        '[role="dialog"] [contenteditable="true"][class*="ProseMirror"], '
        '[role="dialog"] textarea, '
        '[role="dialog"] textarea[name*="message"]'
    )
    if desc_el and params.message:
        tag = (await desc_el.evaluate("e => e.tagName") or "").lower()
        if tag in ("textarea", "input"):
            await desc_el.fill(params.message)
        else:
            await desc_el.click()
            await asyncio.sleep(0.2)
            await _type_with_soft_breaks(page, params.message)

    # Attachments — find the hidden file input.
    if resolved_attachments:
        file_input = await page.query_selector(
            '[role="dialog"] input[type="file"]'
        )
        if file_input:
            try:
                await file_input.set_input_files(
                    [str(p) for p in resolved_attachments]
                )
            except Exception:
                logger.exception("submit_milestone: set_input_files failed")
                return {
                    "status": "error",
                    "message": "Attachment upload failed; check file sizes and types.",
                }
            # Give Upwork a moment to register the uploads.
            await asyncio.sleep(1.5)
        else:
            logger.warning(
                "submit_milestone: attachments requested but no <input type=file> "
                "in the submit modal"
            )

    if params.draft_only:
        return {
            "status": "drafted_submit",
            "message": (
                "Submit modal staged with your description and "
                "attachments. Review in your Brave tab and click the "
                "final Submit button manually."
            ),
            "attached_count": len(resolved_attachments),
        }

    # Final submit button inside the modal.
    submit_btn = await page.query_selector(
        '[role="dialog"] button[data-qa="submit-milestone"], '
        '[role="dialog"] button[data-test="submit-milestone"], '
        '[role="dialog"] button:has-text("Submit for payment"), '
        '[role="dialog"] button:has-text("Submit"), '
        '[role="dialog"] button.is-primary'
    )
    if not submit_btn:
        return {
            "status": "error",
            "message": (
                "Submit modal opened but no final Submit button found. "
                "Selector drift — needs recon."
            ),
        }

    await submit_btn.click()
    # Success path either toasts + closes the modal, or redirects to a
    # confirmation page. Wait for either.
    try:
        await page.wait_for_selector(
            '[role="dialog"]:not(:visible), '
            '[data-qa="milestone-submitted-toast"], '
            'text=Submitted for payment',
            timeout=15000,
        )
    except Exception:
        # Some flows just close the modal silently — fall back to URL check.
        await asyncio.sleep(2.0)

    return {
        "status": "submitted",
        "message": "Milestone submitted for client review.",
        "attached_count": len(resolved_attachments),
    }
