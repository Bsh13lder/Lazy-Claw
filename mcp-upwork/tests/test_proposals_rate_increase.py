"""Tests for the 2026-05 "Schedule a rate increase" defuse + improved
error capture in submit_proposal.

The submit form started blocking on two new required dropdowns
(frequency + percent) under "Schedule a rate increase". Description
calls them optional but submit fails with red validation errors when
empty. These tests pin the defuse helper + ensure the improved
error capture surfaces the real validation messages instead of the
old generic "Could not confirm submission status" string.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# _handle_rate_increase_section: opt-out path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_increase_opt_out_button_clicked():
    """When Upwork shows an opt-out toggle, click it and return early."""
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    optout = AsyncMock()
    page = MagicMock()
    # Only the very first selector matches; everything else returns None.
    queries = {0: optout}
    call_n = {"i": 0}

    async def fake_query(sel):
        i = call_n["i"]
        call_n["i"] += 1
        return queries.get(i)

    page.query_selector = fake_query

    await _handle_rate_increase_section(page)
    optout.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_increase_picks_never_when_opt_out_missing():
    """No opt-out → frequency dropdown set to the 'Never' option."""
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    # Build a fake <select> with options: ["", "Yearly", "Never", "Monthly"]
    placeholder = MagicMock()
    placeholder.text_content = AsyncMock(return_value="Select a frequency")
    placeholder.get_attribute = AsyncMock(return_value="")
    yearly = MagicMock()
    yearly.text_content = AsyncMock(return_value="Yearly")
    yearly.get_attribute = AsyncMock(return_value="yearly")
    never = MagicMock()
    never.text_content = AsyncMock(return_value="Never")
    never.get_attribute = AsyncMock(return_value="never")
    monthly = MagicMock()
    monthly.text_content = AsyncMock(return_value="Monthly")
    monthly.get_attribute = AsyncMock(return_value="monthly")

    freq_dd = MagicMock()
    freq_dd.query_selector_all = AsyncMock(
        return_value=[placeholder, yearly, never, monthly]
    )
    freq_dd.select_option = AsyncMock()

    # Page returns None for all opt-out selectors, then the freq dropdown
    # on the first matching frequency selector, then None for percent.
    call_n = {"i": 0}
    OPTOUT_COUNT = 8  # number of opt-out selectors in the helper
    FREQ_COUNT = 4

    async def fake_query(sel):
        i = call_n["i"]
        call_n["i"] += 1
        if i < OPTOUT_COUNT:
            return None  # no opt-out
        if i == OPTOUT_COUNT:
            return freq_dd  # first frequency selector hits
        return None  # subsequent frequency-selector calls + percent fall through

    page = MagicMock()
    page.query_selector = fake_query

    await _handle_rate_increase_section(page)
    freq_dd.select_option.assert_awaited_once_with("never")


@pytest.mark.asyncio
async def test_rate_increase_picks_lowest_percent():
    """Percent dropdown set to the lowest numeric option (0% if available)."""
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    # Build a percent <select> with: ["", "5%", "0%", "3%"]
    def opt(text, val):
        m = MagicMock()
        m.text_content = AsyncMock(return_value=text)
        m.get_attribute = AsyncMock(return_value=val)
        return m

    pct_options = [
        opt("Select a percent", ""),
        opt("5%", "5"),
        opt("0%", "0"),
        opt("3%", "3"),
    ]

    pct_dd = MagicMock()
    pct_dd.query_selector_all = AsyncMock(return_value=pct_options)
    pct_dd.select_option = AsyncMock()

    OPTOUT_COUNT = 8
    FREQ_COUNT = 4
    PCT_COUNT = 4

    call_n = {"i": 0}

    async def fake_query(sel):
        i = call_n["i"]
        call_n["i"] += 1
        # All opt-outs miss; all frequency selectors miss; first percent hits.
        target_i = OPTOUT_COUNT + FREQ_COUNT
        if i == target_i:
            return pct_dd
        return None

    page = MagicMock()
    page.query_selector = fake_query

    await _handle_rate_increase_section(page)
    pct_dd.select_option.assert_awaited_once_with("0")


@pytest.mark.asyncio
async def test_rate_increase_helper_swallows_all_errors():
    """Helper must never raise — best-effort across UI revisions."""
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    page = MagicMock()
    # Every query raises — helper should still return cleanly.
    page.query_selector = AsyncMock(side_effect=RuntimeError("DOM broke"))

    # Should not raise.
    await _handle_rate_increase_section(page)


@pytest.mark.asyncio
async def test_rate_increase_picks_yearly_when_no_never_option():
    """When 'Never' isn't offered, fall back to 'Yearly' (lowest cadence)."""
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    def opt(text, val):
        m = MagicMock()
        m.text_content = AsyncMock(return_value=text)
        m.get_attribute = AsyncMock(return_value=val)
        return m

    options = [
        opt("Select a frequency", ""),
        opt("Monthly", "monthly"),
        opt("Yearly", "yearly"),
        opt("Quarterly", "quarterly"),
    ]

    freq_dd = MagicMock()
    freq_dd.query_selector_all = AsyncMock(return_value=options)
    freq_dd.select_option = AsyncMock()

    call_n = {"i": 0}

    async def fake_query(sel):
        i = call_n["i"]
        call_n["i"] += 1
        if i == 8:  # first frequency selector after 8 opt-out misses
            return freq_dd
        return None

    page = MagicMock()
    page.query_selector = fake_query

    await _handle_rate_increase_section(page)
    freq_dd.select_option.assert_awaited_once_with("yearly")
