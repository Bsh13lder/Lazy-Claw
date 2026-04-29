"""Tests for browser client."""

import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_profile_dir_exists():
    """Test that profile directory can be created."""
    from upwork_mcp.browser.client import PROFILE_DIR

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    assert PROFILE_DIR.exists()


@pytest.mark.asyncio
async def test_browser_launch(browser):
    """Test that browser can launch."""
    page = await browser.start()
    assert page is not None


@pytest.mark.asyncio
async def test_browser_navigation(browser):
    """Test that browser can navigate."""
    page = await browser.start()
    await page.goto("https://www.upwork.com")
    title = await page.title()
    assert "Upwork" in title or "Work" in title


@pytest.mark.asyncio
async def test_browser_close(browser):
    """Test that browser closes cleanly."""
    await browser.start()
    await browser.close()
    assert browser._page is None
    assert browser._context is None
