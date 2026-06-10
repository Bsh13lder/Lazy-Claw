"""Offer tools for Upwork MCP.

When a client sends a freelancer an offer (fixed-price or hourly), it
appears under `/nx/offers/` until the freelancer either accepts (creating
a contract) or declines. The 2026 redesign also surfaces offers from
within the contracts page header.

Three tools live here:

* :func:`get_offers` — list pending/accepted/declined offers (read-only)
* :func:`accept_offer` — click Accept Offer + confirm (CREATES a contract,
  binds the freelancer to the scope/budget)
* :func:`decline_offer` — click Decline + reason

Both write paths support ``draft_only`` so a caller can stage the modal
without firing the irreversible final click.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..browser.client import get_browser
from .page_state import empty_or_blocked_result

logger = logging.getLogger(__name__)


# ── Candidate landing pages ────────────────────────────────────────────
# Upwork has migrated offer-list URLs multiple times. We probe in order
# and pick the first one that renders a non-empty offer list. The 2026
# layout most commonly lives at /nx/offers/, but legacy users still see
# /ab/messages/ → offers tab. Order matters.
_OFFER_LIST_URLS: tuple[str, ...] = (
    "https://www.upwork.com/nx/offers/",
    "https://www.upwork.com/ab/offers/",
    "https://www.upwork.com/nx/wm/contracts/?status=pending",
)


# ── Pydantic schemas ───────────────────────────────────────────────────


class OffersParams(BaseModel):
    """Parameters for :func:`get_offers`."""

    status: Literal["pending", "accepted", "declined", "all"] = Field(
        default="pending",
        description=(
            "Filter by offer status. 'pending' (default) = offers waiting "
            "for the freelancer's response (the actionable bucket). "
            "'accepted' = offers that already turned into contracts (use "
            "upwork_get_contracts for the contract details). 'declined' = "
            "the freelancer turned down. 'all' = everything."
        ),
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of offers to return (clamped 1-50).",
    )


class AcceptOfferParams(BaseModel):
    """Parameters for :func:`accept_offer`."""

    offer_url: str = Field(
        description=(
            "Full URL of the offer card (returned by get_offers as "
            "'url'). Must be on upwork.com/*/offers/* — accepting a "
            "non-offer URL is refused before any browser nav."
        ),
    )
    draft_only: bool = Field(
        default=True,
        description=(
            "Default TRUE (safe): navigate to the offer page and open "
            "the Accept confirm modal but DO NOT click the final Accept "
            "button — the user confirms manually in their live Brave "
            "tab (returns status='drafted_accept'). Pass an explicit "
            "false ONLY when the user has already approved accepting "
            "this exact offer — the final click creates a binding "
            "contract."
        ),
    )


class DeclineOfferParams(BaseModel):
    """Parameters for :func:`decline_offer`."""

    offer_url: str = Field(description="Full URL of the offer card.")
    reason: str = Field(
        default="Not the right fit",
        description=(
            "Free-text decline reason shown to the client. Keep it "
            "short and neutral. URL/product-pitch guards apply — the "
            "reason lands in the client's notification."
        ),
    )
    draft_only: bool = Field(
        default=False,
        description=(
            "When true, open the decline modal + fill the reason but "
            "DO NOT click the final Decline button."
        ),
    )


# ── Helpers ────────────────────────────────────────────────────────────


async def _wait_for_offers_list(page, timeout_ms: int = 8000) -> bool:
    """Best-effort wait for offer cards to materialize. Returns False
    when nothing matched within ``timeout_ms`` — caller decides whether
    to try the next candidate URL or return an empty list.

    The 2026 layout uses ``[data-qa="offer-tile"]`` on cards; older
    layouts used ``.offer-card`` / ``[data-test="offer-tile"]``.
    """
    try:
        await page.wait_for_selector(
            '[data-qa="offer-tile"], [data-test="offer-tile"], '
            '.offer-card, [data-qa^="offer-item"], '
            'a[href*="/offers/"][href*="?"]',
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _extract_offer(el) -> dict | None:
    """Extract a single offer card → dict. Returns None when the row
    looks like a header / empty-state placeholder."""
    offer: dict[str, str] = {}

    # Job title + URL — Upwork's `.aspx`-era layouts used <a> directly;
    # the 2026 layout has a wrapper <div data-qa="title"> containing the link.
    title_el = await el.query_selector(
        '[data-qa="job-title"] a, [data-test="job-title"] a, '
        'a.job-title, a[href*="/offers/"], h3 a, h4 a'
    )
    if title_el:
        offer["title"] = ((await title_el.text_content()) or "").strip()
        href = await title_el.get_attribute("href")
        if href:
            offer["url"] = (
                href if href.startswith("http") else f"https://www.upwork.com{href}"
            )

    if not offer.get("title"):
        return None

    # Client name
    client_el = await el.query_selector(
        '[data-qa="client-name"], [data-test="client-name"], .client-name'
    )
    if client_el:
        offer["client_name"] = ((await client_el.text_content()) or "").strip()

    # Budget / fixed-price amount
    amt_el = await el.query_selector(
        '[data-qa="amount"], [data-test="amount"], .amount, .budget, [data-qa="budget"]'
    )
    if amt_el:
        offer["amount"] = ((await amt_el.text_content()) or "").strip()

    # Contract type (hourly / fixed)
    type_el = await el.query_selector(
        '[data-qa="contract-type"], [data-test="contract-type"], .contract-type'
    )
    if type_el:
        offer["type"] = ((await type_el.text_content()) or "").strip()

    # Status badge (pending / expired / accepted / declined)
    status_el = await el.query_selector(
        '[data-qa="offer-status"], [data-test="offer-status"], .status-badge'
    )
    if status_el:
        offer["status"] = ((await status_el.text_content()) or "").strip().lower()

    # Sent date / expires
    sent_el = await el.query_selector(
        '[data-qa="sent-date"], [data-test="sent-date"], .sent-date, time'
    )
    if sent_el:
        offer["sent_at"] = ((await sent_el.text_content()) or "").strip()

    return offer


# ── Public tools ───────────────────────────────────────────────────────


async def get_offers(params: OffersParams) -> list[dict] | dict:
    """List offers from the Upwork offers page.

    Probes a sequence of known offer-list URLs (Upwork has migrated this
    surface several times) and returns the first non-empty result. When
    every probe yields zero offers, returns the structured
    ``{"status": "empty_or_blocked", ...}`` dict (see page_state.py) so
    the caller can distinguish "no pending offers" from "Cloudflare
    wall" from "Upwork moved the page again".

    The status filter is applied client-side because the Upwork URL
    query string is unreliable across layouts (some surfaces drop
    ``?status=`` silently).
    """
    browser = get_browser()
    await browser.ensure_logged_in()

    offers: list[dict] = []
    last_url_tried: str | None = None
    page = None

    for url in _OFFER_LIST_URLS:
        last_url_tried = url
        try:
            page = await browser.safe_goto(url)
        except Exception:
            logger.exception("get_offers: safe_goto to %s failed", url)
            continue

        if not await _wait_for_offers_list(page):
            # Empty surface or wrong page — try the next candidate.
            continue

        # The 2026 layout sometimes lazy-loads — give virtualized rows
        # a short window to mount.
        await asyncio.sleep(0.4)

        cards = await page.query_selector_all(
            '[data-qa="offer-tile"], [data-test="offer-tile"], '
            '.offer-card, [data-qa^="offer-item"]'
        )
        for el in cards[: params.limit]:
            try:
                offer = await _extract_offer(el)
                if offer is None:
                    continue
                offers.append(offer)
            except Exception:
                logger.exception("get_offers: failed to extract one card")
                continue

        if offers:
            break

    if not offers:
        logger.warning(
            "get_offers returned empty after probing %s candidate URL(s); "
            "last tried: %s — returning structured empty_or_blocked",
            len(_OFFER_LIST_URLS),
            last_url_tried,
        )
        # `page` is the last successfully navigated handle, or None when
        # every safe_goto raised — empty_or_blocked_result tolerates both.
        return await empty_or_blocked_result(page)

    # Client-side status filter — Upwork's per-URL filtering is flaky.
    if params.status != "all":
        wanted = params.status.lower()
        offers = [
            o
            for o in offers
            if wanted in (o.get("status") or "pending").lower()
        ]

    return offers[: params.limit]


async def accept_offer(params: AcceptOfferParams) -> dict:
    """Accept an Upwork offer (creates a binding contract).

    Workflow:

    1. ``safe_goto`` to the offer URL.
    2. Locate the Accept button.
    3. Click → wait for the confirm modal.
    4. If ``draft_only`` — return ``status='drafted_accept'`` so the user
       can finalize manually.
    5. Otherwise click final confirm → wait for redirect to the new
       contract page.

    Returns ``{status, contract_url?, message}``. Statuses:
      * ``"accepted"``  — final click went through, contract URL returned
      * ``"drafted_accept"`` — modal staged, user must finalize
      * ``"already_accepted"`` — Upwork shows this offer is already a contract
      * ``"not_found"`` — Accept button not on the page
      * ``"error"`` — exception during the flow (details in ``message``)
    """
    if not params.offer_url.startswith("http"):
        return {"status": "error", "message": "offer_url must be a full URL"}
    if "upwork.com" not in params.offer_url:
        return {"status": "error", "message": "offer_url is not on upwork.com"}

    browser = get_browser()
    await browser.ensure_logged_in()

    try:
        page = await browser.safe_goto(params.offer_url)
    except Exception as exc:
        logger.exception("accept_offer: safe_goto failed")
        return {"status": "error", "message": f"navigation failed: {exc}"}

    # Wait for the page to settle.
    await asyncio.sleep(1.2)

    # Already-accepted detection — page redirects to /nx/wm/contracts/<id>
    # or shows an "Accepted" badge.
    current = page.url
    if "/wm/contracts/" in current and "/offers/" not in current:
        return {
            "status": "already_accepted",
            "contract_url": current,
            "message": "Offer is already a contract.",
        }

    accept_btn = await page.query_selector(
        'button[data-qa="accept-offer-btn"], '
        'button[data-test="accept-offer"], '
        'button:has-text("Accept Offer"), '
        'button:has-text("Accept offer"), '
        'a:has-text("Accept Offer")'
    )
    if not accept_btn:
        # Maybe already accepted — look for the badge
        badge = await page.query_selector(
            '[data-qa="status-accepted"], .badge:has-text("Accepted")'
        )
        if badge:
            return {
                "status": "already_accepted",
                "contract_url": current,
                "message": "Offer status shows accepted; no Accept button.",
            }
        return {
            "status": "not_found",
            "message": "Accept Offer button not found on the page.",
        }

    await accept_btn.click()
    # Confirm modal usually appears within ~1s.
    await asyncio.sleep(1.5)

    confirm_btn = await page.query_selector(
        'button[data-qa="confirm-accept-offer"], '
        'button[data-test="confirm-accept"], '
        '[role="dialog"] button:has-text("Accept"), '
        '[role="dialog"] button:has-text("Yes, accept"), '
        '[role="dialog"] button.is-primary'
    )

    if params.draft_only:
        return {
            "status": "drafted_accept",
            "message": (
                "Confirm modal staged — review terms in your Brave tab "
                "and click the final Accept button manually."
            ),
        }

    if not confirm_btn:
        return {
            "status": "error",
            "message": (
                "Accept Offer clicked but confirm modal didn't surface. "
                "Selector may need an update."
            ),
        }

    await confirm_btn.click()
    # Wait for Upwork to redirect to the new contract page.
    try:
        await page.wait_for_url("**/wm/contracts/**", timeout=20000)
    except Exception:
        # Some flows show an interstitial — give it one more beat.
        await asyncio.sleep(2.0)

    final_url = page.url
    return {
        "status": "accepted",
        "contract_url": final_url if "/wm/contracts/" in final_url else None,
        "message": "Offer accepted.",
    }


async def decline_offer(params: DeclineOfferParams) -> dict:
    """Decline an Upwork offer.

    Returns ``{status, message}``. Statuses:
      * ``"declined"`` — final click went through
      * ``"drafted_decline"`` — modal staged, user must finalize
      * ``"blocked"`` — reason text contained a URL or product-pitch token
      * ``"not_found"`` — Decline button not on the page
      * ``"error"`` — exception (details in ``message``)
    """
    # Lazy import to avoid a circular dependency at module load — the
    # guard helpers live in messages.py and depend on browser.client too.
    from .messages import _contains_product_pitch, _contains_url

    if not params.offer_url.startswith("http"):
        return {"status": "error", "message": "offer_url must be a full URL"}
    if "upwork.com" not in params.offer_url:
        return {"status": "error", "message": "offer_url is not on upwork.com"}

    offending = _contains_url(params.reason)
    if offending is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused — decline reason contains URL {offending!r}. "
                f"Upwork strips URLs in client notifications and rewords "
                f"the freelancer's reply, which reads badly. Rephrase "
                f"without the URL."
            ),
            "offending_token": offending,
        }
    pitch = _contains_product_pitch(params.reason)
    if pitch is not None:
        return {
            "status": "blocked",
            "message": (
                f"Refused — decline reason names {pitch!r}. The reason "
                f"surfaces in the client's notification; product-pitch "
                f"branding on Upwork reads as bait-and-switch."
            ),
            "offending_token": pitch,
        }

    browser = get_browser()
    await browser.ensure_logged_in()

    try:
        page = await browser.safe_goto(params.offer_url)
    except Exception as exc:
        logger.exception("decline_offer: safe_goto failed")
        return {"status": "error", "message": f"navigation failed: {exc}"}

    await asyncio.sleep(1.2)

    decline_btn = await page.query_selector(
        'button[data-qa="decline-offer-btn"], '
        'button[data-test="decline-offer"], '
        'button:has-text("Decline"), '
        'a:has-text("Decline")'
    )
    if not decline_btn:
        return {
            "status": "not_found",
            "message": "Decline button not found on the page.",
        }

    await decline_btn.click()
    await asyncio.sleep(1.2)

    # Find the reason textarea / input in the modal.
    reason_input = await page.query_selector(
        '[role="dialog"] textarea, '
        '[role="dialog"] [contenteditable="true"], '
        '[role="dialog"] input[type="text"]'
    )
    if reason_input:
        try:
            await reason_input.fill(params.reason)
        except Exception:
            # Tiptap-style contenteditable can't be .fill()'d — click + type.
            await reason_input.click()
            await page.keyboard.type(params.reason, delay=15)

    if params.draft_only:
        return {
            "status": "drafted_decline",
            "message": (
                "Decline modal staged with your reason. Click the final "
                "Decline button manually in your Brave tab."
            ),
        }

    confirm_btn = await page.query_selector(
        'button[data-qa="confirm-decline-offer"], '
        'button[data-test="confirm-decline"], '
        '[role="dialog"] button:has-text("Decline")'
    )
    if not confirm_btn:
        return {
            "status": "error",
            "message": "Decline modal lacks a confirm button (selector drift?)",
        }

    await confirm_btn.click()
    await asyncio.sleep(2.0)
    return {"status": "declined", "message": "Offer declined."}
