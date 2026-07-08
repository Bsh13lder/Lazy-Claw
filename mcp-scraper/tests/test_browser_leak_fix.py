"""
Regression tests for the 2026-07-03 mcp-scraper OOM incident.

Root cause: `async with AsyncWebCrawler(**cfg) as crawler:` in
`crawler_core.py` relies on Python's context-manager protocol. When
`__aenter__` (crawler.start(), which launches the underlying Chromium /
Playwright process) raises -- e.g. because the CDP handshake never
completes -- `__aexit__` (crawler.close(), which reaps the browser
process) is *never called*. Every failed browser launch inside the
7-stage crawl_url_with_fallback escalation therefore leaked one more
Chromium process until the container's cgroup memory limit was hit and
the kernel OOM-killed it.

These tests exercise `mcp_scraper.core.crawler_core._run_stage_with_browser`
-- the extracted helper that replaces the leaky `async with` pattern -- and
`mcp_scraper._vendor.crawl4ai.browser_manager.BrowserManager.start()`'s
managed-browser/CDP-verification path, which has the same shape of bug.

Before the fix these tests fail (RED): close()/cleanup() is never called
on a failed launch, and there is no launch-timeout or concurrency cap.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mcp_scraper.core.crawler_core as crawler_core
from mcp_scraper.models import CrawlRequest


class _DummyLogger:
    """No-op stand-in for crawl4ai's AsyncLogger — only the methods the
    browser-manager code path actually calls."""

    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _FakeCrawler:
    """Stand-in for AsyncWebCrawler. Lets tests control start()/arun()
    behavior without spinning up a real Playwright/Chromium process."""

    instances = []

    # Configured per-test via monkeypatched class attributes before
    # construction (crawler_core constructs a fresh instance per attempt).
    start_exception = None
    start_delay = None
    arun_result = None

    def __init__(self, **browser_config):
        self.browser_config = browser_config
        self.close = AsyncMock()
        self._start_exception = _FakeCrawler.start_exception
        self._start_delay = _FakeCrawler.start_delay
        self._arun_result = _FakeCrawler.arun_result
        _FakeCrawler.instances.append(self)

    async def start(self):
        if self._start_delay:
            await asyncio.sleep(self._start_delay)
        if self._start_exception:
            raise self._start_exception

    async def arun(self, **kwargs):
        if self._arun_result is not None:
            return self._arun_result
        return MagicMock(success=True)


@pytest.fixture(autouse=True)
def _reset_fake_crawler():
    _FakeCrawler.instances = []
    _FakeCrawler.start_exception = None
    _FakeCrawler.start_delay = None
    _FakeCrawler.arun_result = None
    yield
    _FakeCrawler.instances = []
    _FakeCrawler.start_exception = None
    _FakeCrawler.start_delay = None
    _FakeCrawler.arun_result = None


def _make_request(**overrides) -> CrawlRequest:
    defaults = dict(url="https://example.com", timeout=5)
    defaults.update(overrides)
    return CrawlRequest(**defaults)


def _fake_config():
    return SimpleNamespace(js_code=None)


# ---------------------------------------------------------------------------
# crawler_core._run_stage_with_browser — the actual leak site
# ---------------------------------------------------------------------------


async def test_close_called_when_launch_raises(monkeypatch):
    """A launch failure (simulated CDP handshake failure) must still
    reap the browser via close(), not silently orphan the process."""
    monkeypatch.setattr(crawler_core, "AsyncWebCrawler", _FakeCrawler)
    _FakeCrawler.start_exception = RuntimeError(
        "Chrome launched but CDP not responding after 3s"
    )

    with pytest.raises(RuntimeError):
        await crawler_core._run_stage_with_browser(
            {"browser_type": "chromium"}, _make_request(), _fake_config()
        )

    assert len(_FakeCrawler.instances) == 1
    _FakeCrawler.instances[0].close.assert_awaited_once()


async def test_close_called_on_success_too(monkeypatch):
    """Sanity check: the happy path must still close the browser
    (no regression from the reaping fix)."""
    monkeypatch.setattr(crawler_core, "AsyncWebCrawler", _FakeCrawler)

    result = await crawler_core._run_stage_with_browser(
        {"browser_type": "chromium"}, _make_request(), _fake_config()
    )

    assert result is not None
    assert len(_FakeCrawler.instances) == 1
    _FakeCrawler.instances[0].close.assert_awaited_once()


async def test_close_called_when_arun_raises(monkeypatch):
    """A failure *after* a successful launch (e.g. page crash mid-crawl)
    must also reap the browser."""
    monkeypatch.setattr(crawler_core, "AsyncWebCrawler", _FakeCrawler)

    class _CrashingCrawler(_FakeCrawler):
        async def arun(self, **kwargs):
            raise ConnectionError("Target page crashed")

    monkeypatch.setattr(crawler_core, "AsyncWebCrawler", _CrashingCrawler)

    with pytest.raises(ConnectionError):
        await crawler_core._run_stage_with_browser(
            {"browser_type": "chromium"}, _make_request(), _fake_config()
        )

    assert len(_CrashingCrawler.instances) == 1
    _CrashingCrawler.instances[0].close.assert_awaited_once()


async def test_launch_times_out_fast_instead_of_hanging(monkeypatch):
    """Fail fast: a hung/unresponsive launch must abort within the short
    launch-timeout window, not ride the full (much longer) per-stage
    crawl timeout — and must still close() the crawler."""
    monkeypatch.setattr(crawler_core, "_BROWSER_LAUNCH_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(crawler_core, "AsyncWebCrawler", _FakeCrawler)
    _FakeCrawler.start_delay = 999  # would hang "forever"

    request = _make_request(timeout=999)  # outer timeout deliberately huge
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    with pytest.raises(Exception):
        await asyncio.wait_for(
            crawler_core._run_stage_with_browser(
                {"browser_type": "chromium"}, request, _fake_config()
            ),
            timeout=5,  # test-level safety net only
        )

    elapsed = loop.time() - start_time
    assert elapsed < 2, f"launch failure took {elapsed}s — did not fail fast"
    assert len(_FakeCrawler.instances) == 1
    _FakeCrawler.instances[0].close.assert_awaited_once()


async def test_concurrent_launches_capped(monkeypatch):
    """A burst of parallel crawl_url calls (e.g. multiple EXPLORE
    specialists hitting mcp-scraper at once) must not launch more than
    MCP_SCRAPER_MAX_CONCURRENT_BROWSERS browsers concurrently."""
    monkeypatch.setattr(crawler_core, "_BROWSER_LAUNCH_SEMAPHORE", asyncio.Semaphore(2))

    concurrency = {"current": 0, "max": 0}
    release_event = asyncio.Event()

    class _SlowFakeCrawler(_FakeCrawler):
        async def start(self):
            concurrency["current"] += 1
            concurrency["max"] = max(concurrency["max"], concurrency["current"])
            await release_event.wait()
            concurrency["current"] -= 1

    monkeypatch.setattr(crawler_core, "AsyncWebCrawler", _SlowFakeCrawler)

    request = _make_request()
    tasks = [
        asyncio.create_task(
            crawler_core._run_stage_with_browser(
                {"browser_type": "chromium"}, request, _fake_config()
            )
        )
        for _ in range(5)
    ]
    await asyncio.sleep(0.1)
    assert concurrency["max"] <= 2, (
        f"expected at most 2 concurrent browser launches, saw {concurrency['max']}"
    )

    release_event.set()
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# BrowserManager.start() — the vendored crawl4ai managed-browser/CDP path
# ---------------------------------------------------------------------------


async def test_managed_browser_subprocess_reaped_on_cdp_verify_failure(monkeypatch):
    """End-to-end reproduction of the OOM incident's exact shape: a
    ManagedBrowser spawns a Chromium subprocess, the CDP handshake never
    becomes ready ("Chrome launched but CDP not responding"), and
    BrowserManager.start() must terminate/kill that subprocess before
    raising — not leave it running."""
    from mcp_scraper._vendor.crawl4ai.async_configs import BrowserConfig
    from mcp_scraper._vendor.crawl4ai.browser_manager import BrowserManager

    config = BrowserConfig(use_managed_browser=True, headless=True, debugging_port=9222)
    bm = BrowserManager(browser_config=config, logger=_DummyLogger())

    fake_process = MagicMock()
    fake_process.poll.return_value = None  # never exits on its own

    async def fake_managed_start():
        # Simulate ManagedBrowser.start(): the Chromium subprocess was
        # spawned and is now tracked on the instance...
        bm.managed_browser.browser_process = fake_process
        return "http://localhost:9222"

    bm.managed_browser.start = fake_managed_start
    bm.managed_browser.temp_dir = None
    # ...but the CDP handshake never completes (the incident's exact
    # symptom: "Chrome launched but CDP not responding after 3s").
    bm._verify_cdp_ready = AsyncMock(return_value=False)

    fake_playwright_obj = MagicMock()
    fake_async_playwright = MagicMock()
    fake_async_playwright.return_value.start = AsyncMock(return_value=fake_playwright_obj)

    with patch("playwright.async_api.async_playwright", fake_async_playwright):
        with pytest.raises(Exception, match="CDP endpoint"):
            await bm.start()

    assert fake_process.terminate.called or fake_process.kill.called, (
        "orphaned Chromium subprocess was never terminated/killed after "
        "the CDP handshake failed"
    )
