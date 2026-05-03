"""Browser client for Upwork automation using Patchright with CDP."""

import asyncio
import subprocess
import os
from pathlib import Path
from typing import Any
from patchright.async_api import async_playwright, Browser, BrowserContext, Page


def _resolve_profile_dir() -> Path:
    """Resolve profile dir.

    Honors `LAZYCLAW_BROWSER_PROFILE_DIR` (set by LazyClaw's MCP manager
    so the Upwork MCP shares the same Brave profile + cookies as the rest
    of LazyClaw). Falls back to upstream default for standalone use.
    """
    env_dir = os.environ.get("LAZYCLAW_BROWSER_PROFILE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".upwork-mcp" / "chrome-profile"


def _resolve_cdp_port() -> int:
    """Resolve CDP port from `LAZYCLAW_CDP_PORT` env, fallback 9222."""
    raw = os.environ.get("LAZYCLAW_CDP_PORT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 9222


def _resolve_cdp_host() -> str:
    """Resolve CDP host from `LAZYCLAW_CDP_HOST`, fallback 127.0.0.1.

    Set to `host.docker.internal` when running inside the LazyClaw Docker
    container so the MCP connects to the user's REAL Brave on the Mac/Win
    host (with their cookies and Cloudflare-passing fingerprint), instead
    of trying to launch its own Chrome inside the container (which has no
    Chrome binary).

    KEY DETAIL: we DNS-resolve the hostname to its IP. Chromium's CDP
    HTTP server only accepts `Host:` headers that are an IP address or
    `localhost` — it rejects `host.docker.internal` with a 400. By
    connecting to the resolved IP (e.g. 192.168.65.254) the Host header
    becomes the IP and Brave accepts it. This means the user can launch
    Brave with just `--remote-debugging-port=9222` — no
    `--remote-allow-origins=*` needed.
    """
    import socket
    host = os.environ.get("LAZYCLAW_CDP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def _running_in_container() -> bool:
    """Best-effort check for "running inside a Linux container".

    Used to suppress the in-process `start_chrome_with_debug()` fallback —
    inside the container there's no Chrome binary and no display, so the
    fallback always fails with a confusing error. Better to fail fast and
    tell the user to start Brave on the host with debug port.
    """
    return os.path.exists("/.dockerenv")


PROFILE_DIR = _resolve_profile_dir()
CDP_PORT = _resolve_cdp_port()
CDP_HOST = _resolve_cdp_host()

# Real Chrome paths by platform
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
    "/usr/bin/google-chrome",  # Linux
    "/usr/bin/chromium-browser",  # Linux Chromium
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",  # Windows
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",  # Windows x86
]


def find_chrome() -> str | None:
    """Find real Chrome/Chromium browser on system."""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def is_chrome_running_with_debug() -> bool:
    """Check if Chrome is running with debug port at CDP_HOST:CDP_PORT.

    CDP_HOST defaults to 127.0.0.1 but is set to `host.docker.internal`
    by LazyClaw's MCP manager when running in Docker, so the probe reaches
    the user's host Brave instead of the empty container loopback.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"http://{CDP_HOST}:{CDP_PORT}/json/version", timeout=2,
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_chrome_with_debug() -> bool:
    """Start Chrome with remote debugging enabled."""
    chrome_path = find_chrome()
    if not chrome_path:
        return False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Start Chrome with debugging port
    subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Chrome to start
    for _ in range(10):
        if is_chrome_running_with_debug():
            return True
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.5))

    return is_chrome_running_with_debug()


class UpworkBrowser:
    """Manages browser instance for Upwork automation via CDP."""

    def __init__(self, headless: bool = False, timeout: int = 30000):
        self.headless = headless  # Ignored for CDP mode
        self.timeout = timeout
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._started = False

    async def start(self) -> Page:
        """Connect to Chrome via CDP."""
        if self._started and self._page:
            return self._page

        # Ensure Chrome is running with debug port at CDP_HOST:CDP_PORT.
        if not is_chrome_running_with_debug():
            if _running_in_container():
                # No Chrome inside the LazyClaw Docker image — surface a
                # crisp instruction instead of the cryptic "Could not start
                # Chrome" the host-launch path produces.
                raise RuntimeError(
                    f"Cannot reach a Brave/Chrome with --remote-debugging-port "
                    f"at {CDP_HOST}:{CDP_PORT}. Start Brave on your host with:\n"
                    f"  /Applications/Brave\\ Browser.app/Contents/MacOS/Brave\\ Browser "
                    f"--remote-debugging-port={CDP_PORT} "
                    f"--user-data-dir=$HOME/Library/Application\\ Support/BraveSoftware/Brave-Browser-Lazy\n"
                    f"(or use scripts/install-host-brave-bridge.sh for a launchd-managed copy)."
                )
            print("Starting Chrome with debug port...")
            if not start_chrome_with_debug():
                raise RuntimeError(
                    f"Could not start Chrome. Please start it manually with:\n"
                    f'"{find_chrome()}" --remote-debugging-port={CDP_PORT}'
                )
            await asyncio.sleep(2)

        self._playwright = await async_playwright().start()

        # Connect via CDP — uses CDP_HOST so containerized installs reach
        # the host Brave through host.docker.internal.
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f"http://{CDP_HOST}:{CDP_PORT}"
        )

        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        else:
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()

        self._page.set_default_timeout(self.timeout)
        self._started = True
        return self._page

    async def get_page(self) -> Page:
        """Get or create page instance."""
        if not self._started or not self._page:
            return await self.start()
        return self._page

    async def close(self):
        """Disconnect from browser (doesn't close Chrome)."""
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    async def is_logged_in(self) -> bool:
        """Check if user is authenticated on Upwork."""
        page = await self.get_page()
        try:
            await page.goto("https://www.upwork.com/nx/find-work/best-matches", wait_until="domcontentloaded")

            # Wait for page to stabilize (Cloudflare or content)
            for _ in range(10):
                await asyncio.sleep(2)
                title = await page.title()
                if "moment" not in title.lower():
                    break

            current_url = page.url.lower()
            title = await page.title()

            # Check for Cloudflare (still showing)
            if "moment" in title.lower():
                print("Cloudflare challenge detected. Please solve it in the browser window.")
                return False

            # Check for login redirect
            if "login" in current_url or "ab/account-security" in current_url:
                return False

            return True
        except Exception as e:
            print(f"Login check error: {e}")
            return False

    async def ensure_logged_in(self) -> bool:
        """Verify login status, raise error if not logged in."""
        if not await self.is_logged_in():
            raise RuntimeError(
                "Not logged in to Upwork. Run 'uvx upwork-mcp --login' to authenticate."
            )
        return True

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Page:
        """Navigate to URL and return page."""
        page = await self.get_page()
        await page.goto(url, wait_until=wait_until)
        return page

    async def wait_for_selector(self, selector: str, timeout: int | None = None) -> Any:
        """Wait for element to appear."""
        page = await self.get_page()
        return await page.wait_for_selector(selector, timeout=timeout or self.timeout)

    async def extract_text(self, selector: str, default: str = "") -> str:
        """Extract text content from selector."""
        page = await self.get_page()
        try:
            element = await page.query_selector(selector)
            if element:
                return (await element.text_content() or "").strip()
        except Exception:
            pass
        return default

    async def extract_texts(self, selector: str) -> list[str]:
        """Extract text from all matching elements."""
        page = await self.get_page()
        elements = await page.query_selector_all(selector)
        texts = []
        for el in elements:
            text = await el.text_content()
            if text:
                texts.append(text.strip())
        return texts

    async def extract_attribute(self, selector: str, attribute: str, default: str = "") -> str:
        """Extract attribute value from selector."""
        page = await self.get_page()
        try:
            element = await page.query_selector(selector)
            if element:
                return (await element.get_attribute(attribute)) or default
        except Exception:
            pass
        return default


# Global browser instance
_browser: UpworkBrowser | None = None


def get_browser(headless: bool = False, timeout: int = 30000) -> UpworkBrowser:
    """Get or create global browser instance."""
    global _browser
    if _browser is None:
        _browser = UpworkBrowser(headless=headless, timeout=timeout)
    return _browser


async def close_browser():
    """Close global browser instance."""
    global _browser
    if _browser:
        await _browser.close()
        _browser = None
