"""Tests for the Air3 dropdown-aware ``_handle_rate_increase_section``.

Verified against the real Upwork DOM on 2026-05-05:

* Section: ``[data-test="sri-input"]``
* Two combobox toggles: ``[data-test="dropdown-toggle"]`` (frequency, percent)
* Options: ``.air3-menu-item[role="option"]``
* Real frequency options: ``Never``, ``Every 3/6/12 months``
* Real percent options: ``5%``, ``10%``, ``15%``, ``Custom``

The older tests assumed native ``<select>`` elements (which don't exist on
Upwork's apply form) and have been replaced.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def _option(text: str, *, role: str = "option", cls: str = "air3-menu-item"):
    """Mock element that responds to text_content() + click()."""
    el = MagicMock()
    el.text_content = AsyncMock(return_value=text)
    el.click = AsyncMock()
    return el


# ---------------------------------------------------------------------------
# 1. Section absent → no-op (don't touch random comboboxes elsewhere)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_op_when_section_absent():
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    await _handle_rate_increase_section(page)

    # query_selector_all should never be invoked when section missing
    assert not page.query_selector_all.called


# ---------------------------------------------------------------------------
# 2. Picks "Never" for frequency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_picks_never_for_frequency():
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    section = MagicMock()
    freq_toggle = AsyncMock()
    pct_toggle = AsyncMock()
    pct_toggle.is_visible = AsyncMock(return_value=False)  # collapses post-Never

    section.query_selector_all = AsyncMock(return_value=[freq_toggle, pct_toggle])

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=section)

    # First call returns frequency options; second (after pct click) won't fire
    # because pct_toggle.is_visible() == False.
    freq_options = [
        _option("Never"),
        _option("Every 3 months"),
        _option("Every 6 months"),
        _option("Every 12 months"),
    ]
    page.query_selector_all = AsyncMock(return_value=freq_options)

    await _handle_rate_increase_section(page)

    # Frequency toggle clicked
    freq_toggle.click.assert_awaited_once()
    # "Never" option clicked
    freq_options[0].click.assert_awaited_once()
    # Other options NOT clicked
    for opt in freq_options[1:]:
        opt.click.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Falls back to "Every 12 months" when "Never" missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_falls_back_to_yearly_when_never_missing():
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    section = MagicMock()
    freq_toggle = AsyncMock()
    pct_toggle = AsyncMock()
    pct_toggle.is_visible = AsyncMock(return_value=False)
    section.query_selector_all = AsyncMock(return_value=[freq_toggle, pct_toggle])

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=section)

    options = [
        _option("Every 3 months"),
        _option("Every 6 months"),
        _option("Every 12 months"),
    ]
    page.query_selector_all = AsyncMock(return_value=options)

    await _handle_rate_increase_section(page)

    # 12 months picked
    options[2].click.assert_awaited_once()
    options[0].click.assert_not_called()
    options[1].click.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Picks lowest numeric percent, never "Custom"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_picks_lowest_percent_skipping_custom():
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    section = MagicMock()
    freq_toggle = AsyncMock()
    pct_toggle = AsyncMock()
    pct_toggle.is_visible = AsyncMock(return_value=True)

    # First call: original toggles list (used for [0] click + len check)
    # Second call: re-queried toggles after frequency pick
    section.query_selector_all = AsyncMock(
        side_effect=[[freq_toggle, pct_toggle], [freq_toggle, pct_toggle]]
    )

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=section)

    freq_options = [_option("Never")]
    pct_options = [_option("5%"), _option("10%"), _option("15%"), _option("Custom")]
    page.query_selector_all = AsyncMock(side_effect=[freq_options, pct_options])

    await _handle_rate_increase_section(page)

    # Percent toggle was clicked, "5%" picked, "Custom" never clicked
    pct_toggle.click.assert_awaited_once()
    pct_options[0].click.assert_awaited_once()  # "5%"
    pct_options[3].click.assert_not_called()  # "Custom"


# ---------------------------------------------------------------------------
# 5. Wrong toggle count → bail out (don't click random widgets)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bails_when_toggle_count_unexpected():
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    section = MagicMock()
    only_one = AsyncMock()
    section.query_selector_all = AsyncMock(return_value=[only_one])

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=section)
    page.query_selector_all = AsyncMock()

    await _handle_rate_increase_section(page)

    # No toggle was clicked because the count didn't match expectation.
    only_one.click.assert_not_called()


# ---------------------------------------------------------------------------
# 6. All errors swallowed — never raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swallows_all_errors():
    from upwork_mcp.tools.proposals import _handle_rate_increase_section

    page = MagicMock()
    page.query_selector = AsyncMock(side_effect=RuntimeError("DOM broke"))

    # Should not raise.
    await _handle_rate_increase_section(page)
