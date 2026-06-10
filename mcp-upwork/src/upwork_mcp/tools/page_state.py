"""Shared "empty vs blocked" page-state diagnosis for read tools.

Problem (2026-06-10 audit): read paths returned bare ``[]`` / ``{}`` on
selector miss or page failure with diagnostics only in stderr logs — the
calling brain could not tell "genuinely no results" from "Cloudflare
wall" from "session expired" and quietly reported empty inboxes / job
boards. 53 occurrences of the get_messages "0 room elements" warning in
one day of logs.

Fix: every zero-item extraction returns the structured dict built by
:func:`empty_or_blocked_result` instead of the bare empty value.

The Cloudflare / login markers mirror the detection already used in
``browser/client.py:safe_goto`` (CF-pass loop) and
``tools/proposals.py:_wait_for_cloudflare_clear`` — centralized here so
the five read tools share one diagnosis instead of five drifting copies.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DIAG_CLOUDFLARE = "cloudflare_challenge"
DIAG_LOGIN = "login_required"
DIAG_DRIFT = "selector_drift_or_truly_empty"

# URL marker used by safe_goto's CF-pass loop.
_CF_URL_MARKER = "challenges.cloudflare.com"
# Title markers used by _wait_for_cloudflare_clear ("Just a moment...",
# "Checking your browser...").
_CF_TITLE_MARKERS = ("moment", "checking your browser")
# Body markers used by safe_goto's CF-pass loop (first 2 KB of content).
_CF_BODY_MARKERS = ("just a moment", "cf-browser-verification")
# URL markers used by is_logged_in's login-redirect check.
_LOGIN_URL_MARKERS = ("/login", "ab/account-security")

_HINTS = {
    DIAG_CLOUDFLARE: (
        "Upwork is showing a Cloudflare challenge — open Brave on the "
        "host, solve it manually, then retry this tool."
    ),
    DIAG_LOGIN: (
        "Upwork redirected to the login page — the session expired. "
        "Sign in to Upwork in Brave, then retry this tool."
    ),
    DIAG_DRIFT: (
        "Either this surface is genuinely empty or Upwork's page layout "
        "drifted past the extraction selectors — verify via your own "
        "browser tool before concluding there are no results."
    ),
}


async def diagnose_page_state(page) -> tuple[str, str, str]:
    """Classify the page's current state for a zero-item extraction.

    Returns ``(diagnosis, page_url, page_title)``. Never raises — a dead
    page handle mid-diagnosis degrades to the drift diagnosis with
    whatever state was readable.
    """
    url = ""
    title = ""
    body = ""
    if page is not None:
        try:
            url = page.url or ""
        except Exception:
            url = ""
        try:
            title = (await page.title()) or ""
        except Exception:
            title = ""
        try:
            body = ((await page.content()) or "")[:2000]
        except Exception:
            body = ""

    low_url = url.lower()
    low_title = title.lower()
    low_body = body.lower()

    if (
        _CF_URL_MARKER in low_url
        or any(m in low_title for m in _CF_TITLE_MARKERS)
        or any(m in low_body for m in _CF_BODY_MARKERS)
    ):
        return DIAG_CLOUDFLARE, url, title
    if any(m in low_url for m in _LOGIN_URL_MARKERS):
        return DIAG_LOGIN, url, title
    return DIAG_DRIFT, url, title


async def empty_or_blocked_result(page) -> dict:
    """Build the structured zero-item payload for the calling agent.

    Returned instead of a bare ``[]`` / ``{}`` so the brain can tell
    "no results" from "blocked" and act on the hint. ``page`` may be
    None (e.g. every navigation attempt raised) — falls back to the
    drift diagnosis with placeholder url/title.
    """
    diagnosis, url, title = await diagnose_page_state(page)
    logger.warning(
        "empty_or_blocked: diagnosis=%s url=%s title=%r",
        diagnosis, url or "?", (title or "?")[:80],
    )
    return {
        "status": "empty_or_blocked",
        "items": [],
        "page_url": url or "?",
        "page_title": title or "?",
        "diagnosis": diagnosis,
        "hint": _HINTS[diagnosis],
    }
