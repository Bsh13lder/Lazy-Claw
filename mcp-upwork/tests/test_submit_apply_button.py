"""Pin Apply-button selector flexibility + form-already-open path
+ already-applied detection in submit_proposal.

The 2026-05 production trace showed submit_proposal returning
"Apply button not found" when the brain navigated to a job URL —
either Upwork rotated the data-test attr or the job page already
had a saved-draft proposal form open. Both paths now handled.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_submit_skips_apply_click_when_form_already_open():
    """Cover-letter textarea visible on first render → skip Apply click."""
    from upwork_mcp.tools import proposals
    from upwork_mcp.tools.proposals import submit_proposal, SubmitProposalParams

    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock(side_effect=Exception("no success el"))

    cover_textarea = AsyncMock()
    submit_btn = AsyncMock()

    # Form sentinel found on first call → no Apply click path executes.
    proposal_form_sentinel = MagicMock()

    async def query(sel: str):
        if "cover-letter" in sel or "Cover letter" in sel or "proposal-form" in sel:
            return proposal_form_sentinel if not query._cover_consumed else cover_textarea
        if "submit-proposal" in sel or "Submit" in sel or "Send" in sel:
            return submit_btn
        return None

    query._cover_consumed = False  # first match = sentinel; later = real textarea

    # Slightly more honest: hand back textarea on the actual fill call.
    calls = {"i": 0}

    async def query_real(sel: str):
        calls["i"] += 1
        if calls["i"] == 1 and ("cover-letter" in sel or "proposal-form" in sel):
            return proposal_form_sentinel  # form-open sentinel
        if "cover" in sel.lower() or "textarea" in sel.lower():
            return cover_textarea
        if "submit" in sel.lower() or "Send" in sel:
            return submit_btn
        return None

    page.query_selector = query_real
    page.query_selector_all = AsyncMock(return_value=[])
    page.url = "https://www.upwork.com/nx/proposals/job/~01abc/apply/"

    browser = MagicMock()
    browser.ensure_logged_in = AsyncMock(return_value=True)
    browser.get_page = AsyncMock(return_value=page)
    proposals.get_browser = lambda: browser  # type: ignore

    params = SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~01abc",
        cover_letter="Hello",
    )

    result = await submit_proposal(params)
    # Path executed without raising; submit_btn was clicked.
    submit_btn.click.assert_awaited()
    # And we did NOT bail with "Apply button not found".
    assert result.get("message", "") != "Apply button not found"


@pytest.mark.asyncio
async def test_submit_detects_already_applied():
    """When the job page shows 'View Proposal' / 'Withdraw', say so."""
    from upwork_mcp.tools import proposals
    from upwork_mcp.tools.proposals import submit_proposal, SubmitProposalParams

    page = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="My Proposal — Senior Python")
    page.url = "https://www.upwork.com/jobs/~01abc"

    async def query(sel: str):
        s = sel.lower()
        # Form not open, no Apply button, but "View Proposal" present.
        if "cover-letter" in s or "cover" in s or "proposal-form" in s:
            return None
        if "apply" in s:
            return None
        if "view proposal" in s.lower() or "view-proposal" in s.lower() or "withdraw" in s.lower() or "already-applied" in s.lower():
            return MagicMock()
        return None

    page.query_selector = query

    browser = MagicMock()
    browser.ensure_logged_in = AsyncMock(return_value=True)
    browser.get_page = AsyncMock(return_value=page)
    proposals.get_browser = lambda: browser  # type: ignore

    params = SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~01abc",
        cover_letter="Hello",
    )

    result = await submit_proposal(params)
    assert result["status"] == "error"
    assert result.get("already_applied") is True
    assert "Already applied" in result["message"]


@pytest.mark.asyncio
async def test_submit_apply_button_selector_list_is_fat():
    """Selector list must include modern Upwork variants, not just one."""
    import inspect
    from upwork_mcp.tools import proposals
    src = inspect.getsource(proposals.submit_proposal)
    # All variants we've seen in the wild as of 2026-05.
    for needed in (
        '"apply-button"',
        '"apply-now-button"',
        'aria-label*="Apply"',
        'Apply Now',
        'Submit a Proposal',
    ):
        assert needed in src, f"Missing apply-button variant: {needed}"


@pytest.mark.asyncio
async def test_submit_error_includes_url_and_title_when_no_apply():
    """When nothing matches, error gives the brain real evidence."""
    from upwork_mcp.tools import proposals
    from upwork_mcp.tools.proposals import submit_proposal, SubmitProposalParams

    page = MagicMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Page Not Found - Upwork")
    page.url = "https://www.upwork.com/404"

    page.query_selector = AsyncMock(return_value=None)  # nothing matches

    browser = MagicMock()
    browser.ensure_logged_in = AsyncMock(return_value=True)
    browser.get_page = AsyncMock(return_value=page)
    proposals.get_browser = lambda: browser  # type: ignore

    params = SubmitProposalParams(
        job_url="https://www.upwork.com/jobs/~01abc",
        cover_letter="Hello",
    )

    result = await submit_proposal(params)
    assert result["status"] == "error"
    assert result.get("already_applied") is False
    assert "https://www.upwork.com/404" in result["message"]
    assert "Page Not Found" in result["message"]
    assert result["page_url"] == "https://www.upwork.com/404"
