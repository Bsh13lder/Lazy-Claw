"""Browser client for Upwork automation using Patchright with CDP."""

import asyncio
import logging
import subprocess
import os
from pathlib import Path
from typing import Any
from patchright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


# ── Module-level navigation lock ─────────────────────────────────────
# Two MCP tool calls in flight (e.g. get_messages running while search_jobs
# also fires) race on the same Playwright Page handle — one calls
# ``page.goto`` while the other is mid-extract, the tab navigates, the
# extract returns garbage / Target-closed. Symptom in logs:
#     "Upwork browser handle stale, reconnecting via CDP"  (10×/hour)
#     "Page.goto: Target page, context or browser has been closed"
# Serialize all navigation under this lock. Cheap, deterministic, and
# doesn't slow down anything because Upwork's CDN dominates wall time.
_NAV_LOCK = asyncio.Lock()


# Page titles / URL fragments that the legacy fallback selectors used
# to capture instead of the real profile data — never store these as
# the user's name / title / skill list.
_NAV_LABEL_NOISE = frozenset({
    "settings", "edit profile", "my profile", "messages", "find work",
    "find work feed", "best matches", "saved jobs", "proposals",
    "contracts", "all job posts", "reports", "skip skills",
    "previous skills. update list",
    # Row-action buttons that the search-results extractor used to pick
    # up as if they were skill chips.
    "job feedback", "save job", "unsave job", "hide job", "report job",
    "apply now", "apply",
    # Conversation-page CTAs that the contact-name fallback used to
    # capture as if they were the client's name.
    "schedule a meeting", "schedule meeting",
    "search for:", "search for", "filter", "filters",
    "all messages", "all rooms", "all conversations",
    "send", "send message", "more options",
})


def _is_nav_noise(value: str) -> bool:
    """True if a scraped string is one of Upwork's navigation labels."""
    return value.strip().lower() in _NAV_LABEL_NOISE


def _looks_like_upwork(page) -> bool:
    """True if a page handle is currently on the upwork.com origin."""
    try:
        if page.is_closed():
            return False
        return "upwork.com" in (page.url or "").lower()
    except Exception:
        return False


async def _pick_upwork_page(browser):
    """Walk every context + page in the connected browser, prefer an
    existing tab that's already on upwork.com.

    Falls back to the first non-closed page if no Upwork tab is open.
    This is the single biggest fix for the Cloudflare-challenge flow:
    if the user has a logged-in Upwork tab open and we navigate IT,
    Cloudflare passes us straight through. If we navigate a generic
    fresh tab (e.g. their YouTube tab) Cloudflare sees a cold visit
    and throws up a JS-challenge wall even with cookies attached.
    """
    contexts = browser.contexts if browser else []
    upwork_candidates = []
    fallback = None
    for ctx in contexts:
        for p in ctx.pages:
            try:
                if p.is_closed():
                    continue
                if _looks_like_upwork(p):
                    upwork_candidates.append((ctx, p))
                elif fallback is None:
                    fallback = (ctx, p)
            except Exception:
                continue
    if upwork_candidates:
        # Prefer non-`/ab/messages/rooms/<id>` tabs for goto-heavy tools
        # so we don't navigate the user away from an open conversation.
        for ctx, p in upwork_candidates:
            try:
                url = (p.url or "").lower()
            except Exception:
                url = ""
            if "/ab/messages/rooms/" not in url:
                return ctx, p
        return upwork_candidates[0]
    return fallback


class BrowserDisconnectedError(RuntimeError):
    """Raised when the underlying CDP page/browser handle is no longer alive.

    Distinguishes "the connection died between calls" from "user is not
    logged in" — historically swallowed by a bare ``except Exception`` that
    returned False, causing every disconnect to surface as a misleading
    "Not logged in. Run 'uvx upwork-mcp --login'" error and pushing the
    LazyClaw brain to fabricate that fake CLI command in chat.
    """


# Substrings Playwright/Patchright use when the underlying connection is gone.
# Matched against ``str(exc)`` because the exception classes vary across
# Playwright versions and we don't want a hard import dependency on internals.
_DISCONNECT_HINTS = (
    "Target closed",
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "disconnected",
    "Page closed",
    "Connection closed",
    "BrowserContext closed",
)


def _is_disconnect_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(h in msg for h in _DISCONNECT_HINTS)


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
        # Captured by is_logged_in for use in ensure_logged_in's error
        # message — gives the brain real evidence (current url + reason)
        # instead of a hardcoded "run uvx upwork-mcp --login" lie.
        self._last_state_url: str = ""
        self._last_state_reason: str = ""

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

        # Prefer an EXISTING Upwork tab over the generic contexts[0]/pages[0]
        # default. Picking a tab that's already on upwork.com means Cloudflare
        # sees a warm session and lets every subsequent goto through without a
        # JS challenge. Picking a YouTube tab (or any non-Upwork pages[0])
        # makes Cloudflare treat the first goto as a cold visit and block it
        # — exactly the upwork_get_job_details "cloudflare_blocked" failure.
        picked = await _pick_upwork_page(self._browser)
        if picked is not None:
            self._context, self._page = picked
        else:
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
            else:
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()

        self._page.set_default_timeout(self.timeout)
        self._started = True
        return self._page

    def _page_is_alive(self) -> bool:
        """Cheap synchronous check: is the cached page handle still usable?

        Without this, the singleton happily returns a stale Page across MCP
        calls. The first await on it (e.g. ``page.goto``) raises a
        Playwright "Target closed" / "disconnected" error, which the bare
        ``except`` in ``is_logged_in`` swallows as "not logged in" — root
        cause of the 20ms-fail "Run uvx upwork-mcp --login" lie.
        """
        if not self._started or not self._page or not self._browser:
            return False
        try:
            if self._page.is_closed():
                return False
            if not self._browser.is_connected():
                return False
        except Exception:
            return False
        return True

    async def get_page(self) -> Page:
        """Get or create page instance.

        Verifies the cached singleton page is still alive before returning.
        On a stale handle (CDP socket reset, tab crashed, or user closed
        the tab), recreate the connection rather than handing the dead
        handle to callers.

        Also: if the cached page drifted off upwork.com (the user opened a
        new tab on a different origin and the existing Upwork tab is now
        further back), try to re-pick a real Upwork tab before falling
        back to the stale handle. Cookies are shared at the context level
        so this only changes WHICH tab we drive, never the auth state.
        """
        if not self._page_is_alive():
            if self._started:
                logger.warning("Upwork browser handle stale, reconnecting via CDP")
                await self.close()
            return await self.start()
        # Page is alive but may be on a non-upwork origin (e.g. the user
        # alt-tabbed to YouTube). Try to swap to an existing Upwork tab —
        # cheap because we don't reconnect, just re-pick.
        if not _looks_like_upwork(self._page) and self._browser is not None:
            picked = await _pick_upwork_page(self._browser)
            if picked is not None:
                ctx, p = picked
                if _looks_like_upwork(p):
                    self._context = ctx
                    self._page = p
                    p.set_default_timeout(self.timeout)
        return self._page

    async def safe_goto(
        self,
        url: str,
        wait_until: str = "networkidle",
        warm: bool = True,
        cloudflare_retry_s: int = 15,
    ) -> Page:
        """Navigate to a URL with Cloudflare-resilient semantics.

        Three things this does that a bare ``page.goto`` does not:

        1. Serialize concurrent navigations via the module-level
           ``_NAV_LOCK`` so two MCP tools in flight don't race and
           collapse each other's tab into "Target closed".
        2. Warm the session: if the picked tab isn't currently on
           upwork.com, navigate to /nx/find-work/ first and dwell so
           Cloudflare sees a logged-in browsing session before we
           jump to a deep ``/jobs/~<id>`` URL.
        3. Wait out Cloudflare's JS challenge: if the final URL contains
           ``challenges.cloudflare.com`` or the page body still says
           "just a moment", poll for clearance up to
           ``cloudflare_retry_s`` seconds. Real users wait this out;
           we should too.
        """
        async with _NAV_LOCK:
            page = await self.get_page()

            if warm and not _looks_like_upwork(page):
                try:
                    await page.goto(
                        "https://www.upwork.com/nx/find-work/",
                        wait_until="domcontentloaded",
                    )
                    await asyncio.sleep(1.5)
                except Exception as exc:
                    logger.debug("upwork warmup nav failed: %s — proceeding", exc)

            await page.goto(url, wait_until=wait_until)

            # Cloudflare-pass retry loop. Cheap polling — no busy wait.
            for _ in range(cloudflare_retry_s):
                try:
                    current = (page.url or "").lower()
                    body_lower = ((await page.content()) or "").lower()[:2000]
                except Exception:
                    break
                cf_url = "challenges.cloudflare.com" in current
                cf_body = (
                    "just a moment" in body_lower
                    or "cf-browser-verification" in body_lower
                )
                if not cf_url and not cf_body:
                    break
                await asyncio.sleep(1)

            return page

    async def close(self):
        """Disconnect from browser (doesn't close Chrome)."""
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("Playwright stop raised during close (ignored)", exc_info=True)
            self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    async def is_logged_in(self) -> bool:
        """Check if user is authenticated on Upwork.

        Raises ``BrowserDisconnectedError`` when the underlying CDP
        connection is gone — that's a "your bridge is broken" condition,
        not a login state, and the caller (``ensure_logged_in``) handles
        it by reconnecting + retrying once.

        Cheap path: if the picked tab is ALREADY on upwork.com and not on a
        login redirect / cloudflare interstitial, accept the existing
        session without forcing a navigation. The previous behavior
        navigated to ``/nx/find-work/best-matches`` on every MCP call,
        which triggered a fresh Cloudflare check from a tab that was
        already logged in on ``/ab/messages/rooms/<id>`` — manifesting
        as a ``cloudflare_challenge`` raise and the brain bailing out.
        """
        page = await self.get_page()
        try:
            # Cheap-path: trust an existing logged-in Upwork tab.
            current_url_now = (page.url or "").lower()
            if "upwork.com" in current_url_now:
                try:
                    title_now = (await page.title() or "").lower()
                except Exception:
                    title_now = ""
                on_login = (
                    "login" in current_url_now
                    or "ab/account-security" in current_url_now
                )
                on_cf = "moment" in title_now or "challenges.cloudflare.com" in current_url_now
                if not on_login and not on_cf:
                    self._last_state_url = current_url_now
                    self._last_state_reason = "ok_existing_tab"
                    return True

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
                self._last_state_url = current_url
                self._last_state_reason = "cloudflare_challenge"
                logger.info("Upwork: Cloudflare challenge present at %s", current_url)
                return False

            # Check for login redirect
            if "login" in current_url or "ab/account-security" in current_url:
                self._last_state_url = current_url
                self._last_state_reason = "login_redirect"
                return False

            self._last_state_url = current_url
            self._last_state_reason = "ok"
            return True
        except Exception as e:
            if _is_disconnect_error(e):
                raise BrowserDisconnectedError(str(e)) from e
            logger.warning("Upwork is_logged_in unexpected error: %s", e)
            self._last_state_url = ""
            self._last_state_reason = f"check_error: {type(e).__name__}: {e}"[:200]
            return False

    async def ensure_logged_in(self) -> bool:
        """Verify login status with one auto-reconnect retry.

        On disconnect: rebuild the Playwright connection once and retry —
        a stale singleton between MCP tool calls is the common case and
        the user shouldn't see a misleading "not logged in" for it.

        On a real not-logged-in result: raise with the actual current URL
        and reason (cloudflare / login redirect / unexpected). The brain
        sees evidence and stops fabricating ``uvx upwork-mcp --login``.
        """
        try:
            ok = await self.is_logged_in()
        except BrowserDisconnectedError as exc:
            logger.warning("Upwork CDP disconnected during login check: %s — reconnecting once", exc)
            await self.close()
            try:
                ok = await self.is_logged_in()
            except BrowserDisconnectedError as exc2:
                raise RuntimeError(
                    f"Upwork browser bridge unreachable: {exc2}. "
                    f"Check Brave is running on the host with --remote-debugging-port "
                    f"and the LazyClaw host bridge is active."
                ) from exc2

        if not ok:
            reason = getattr(self, "_last_state_reason", "unknown") or "unknown"
            current = getattr(self, "_last_state_url", "") or "(unknown url)"
            if reason == "cloudflare_challenge":
                raise RuntimeError(
                    f"Upwork is showing a Cloudflare 'Verify you are human' challenge "
                    f"at {current}. Open Brave on the host, solve it, then retry."
                )
            if reason == "login_redirect":
                raise RuntimeError(
                    f"Upwork session expired or signed out (redirected to {current}). "
                    f"Open Brave at https://www.upwork.com/nx/find-work, sign in, "
                    f"then retry."
                )
            raise RuntimeError(
                f"Upwork login check failed ({reason}). Current page: {current}. "
                f"Open Brave at https://www.upwork.com/nx/find-work to verify."
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
