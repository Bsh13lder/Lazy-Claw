"""Pytest configuration and fixtures for Upwork MCP tests."""

import pytest
import asyncio
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def browser():
    """Create browser instance for tests."""
    from upwork_mcp.browser.client import UpworkBrowser

    browser = UpworkBrowser(headless=True, timeout=30000)
    yield browser
    await browser.close()
