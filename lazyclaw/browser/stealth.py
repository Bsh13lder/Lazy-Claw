"""Browser stealth: anti-detection + Cloudflare auto-solve for CDP browsers.

Approach: desktop Brave (real browser, legit) + hide automation signals.
No fake mobile UA — just don't look like a bot.

1. navigator.webdriver masking + fingerprint fixes
2. Cloudflare challenge detection + auto-solve via touch click
3. Smart page load wait (DOM mutation observer)
"""

from __future__ import annotations

import asyncio
import logging
import random

logger = logging.getLogger(__name__)

# -- Stealth JS injection --------------------------------------------------

# Injected BEFORE any page script via Page.addScriptToEvaluateOnNewDocument
STEALTH_JS = """
// Hide navigator.webdriver (primary bot detection signal)
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
});

// Fix navigator.plugins (headless has empty plugins array)
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
        {name: 'Brave Ad Block Updater', filename: 'cffkpbalmllkdoenhmdmpbkajipdjfam'},
    ],
    configurable: true,
});

// Fix navigator.languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
    configurable: true,
});

// Fix chrome.runtime (present in real Chrome/Brave, missing in headless)
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        connect: function() {},
        sendMessage: function() {},
    };
}

// Fix permissions query (headless returns 'denied' for notifications)
const _origPermQuery = window.navigator.permissions?.query;
if (_origPermQuery) {
    window.navigator.permissions.query = (params) => {
        if (params.name === 'notifications') {
            return Promise.resolve({state: Notification.permission});
        }
        return _origPermQuery.call(navigator.permissions, params);
    };
}

// Remove CDP-specific properties that Cloudflare checks
for (const key of Object.keys(window)) {
    if (key.startsWith('cdc_') || key.startsWith('__webdriver')) {
        try { delete window[key]; } catch(e) {}
    }
}
"""

# -- Cloudflare detection --------------------------------------------------

CLOUDFLARE_PATTERNS = (
    "Just a moment",
    "Checking your browser",
    "Verify you are human",
    "challenges.cloudflare.com",
    "cf-browser-verification",
    "Attention Required",
)

# JS to find and return Cloudflare challenge clickable element position
CLOUDFLARE_FIND_CHALLENGE_JS = """
(() => {
    // Turnstile checkbox (inside iframe)
    const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare"]');
    for (const iframe of iframes) {
        const rect = iframe.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
            // Click center of the iframe (Turnstile checkbox is usually centered)
            return JSON.stringify({
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
                found: 'turnstile_iframe',
            });
        }
    }

    // "Verify you are human" button or checkbox
    const selectors = [
        'input[type="checkbox"]',
        '[id*="challenge"]',
        'button[type="submit"]',
        '.cf-turnstile',
        '#cf-please-wait',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                return JSON.stringify({
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    found: sel,
                });
            }
        }
    }

    return JSON.stringify({found: null});
})()
"""

# JS to check if DOM has settled (no mutations for N ms)
DOM_SETTLED_JS = """
new Promise((resolve) => {
    let timer;
    const observer = new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(() => { observer.disconnect(); resolve(true); }, 500);
    });
    observer.observe(document.body || document.documentElement, {
        childList: true, subtree: true, attributes: true,
    });
    // Fallback: resolve after 3s even if DOM keeps changing
    setTimeout(() => { observer.disconnect(); resolve(true); }, 3000);
    // If DOM is already idle, resolve after 500ms
    timer = setTimeout(() => { observer.disconnect(); resolve(true); }, 500);
})
"""


# -- Public API ------------------------------------------------------------

async def apply_stealth(conn) -> None:
    """Apply anti-detection measures to a CDP connection.

    Call AFTER connecting to a tab, BEFORE navigating.
    Desktop mode — no fake UA, just hide automation signals.
    """
    # 1. Inject stealth JS on every new document
    try:
        await conn.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": STEALTH_JS},
        )
    except Exception as exc:
        logger.warning("Failed to inject stealth JS: %s", exc)

    # 2. Patch screenX/screenY CDP bug (Cloudflare Turnstile checks this)
    try:
        from lazyclaw.browser.human_input import apply_screen_patch
        await apply_screen_patch(conn)
    except Exception as exc:
        logger.warning("Failed to inject screen patch: %s", exc)

    # 3. Enable touch (legit — many laptops have touchscreens)
    try:
        await conn.send(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": True, "maxTouchPoints": 5},
        )
    except Exception as exc:
        logger.warning("Failed to enable touch: %s", exc)

    logger.info("Stealth applied (anti-detection + screen patch + touch)")


async def wait_for_page_ready(conn, timeout: float = 5.0) -> bool:
    """Wait for page DOM to settle (no mutations for 500ms).

    Better than fixed sleep — adapts to fast and slow pages.
    Returns True when settled, False on timeout.
    """
    try:
        result = await asyncio.wait_for(
            conn.send("Runtime.evaluate", {
                "expression": DOM_SETTLED_JS,
                "awaitPromise": True,
            }),
            timeout=timeout,
        )
        return True
    except (asyncio.TimeoutError, Exception):
        return False


async def detect_and_solve_cloudflare(conn, timeout: float = 20.0) -> bool:
    """Detect Cloudflare challenge and attempt to solve via touch click.

    Flow:
    1. Check if page is a Cloudflare challenge
    2. Find the verify checkbox/button
    3. Touch-click it (simulates touchscreen tap)
    4. Wait for challenge to resolve
    5. Return True if page loaded past Cloudflare

    Returns False if not a challenge or couldn't solve it.
    """
    # Check if this is actually a Cloudflare page
    try:
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": "document.title + ' ' + (document.body?.innerText?.substring(0, 300) || '')"},
        )
        page_text = result.get("result", {}).get("value", "")
    except Exception:
        return False

    is_challenge = any(p in page_text for p in CLOUDFLARE_PATTERNS)
    if not is_challenge:
        return True  # Not a challenge — page is fine

    logger.info("Cloudflare challenge detected, attempting auto-solve...")

    # Wait a moment for Turnstile widget to render
    await asyncio.sleep(random.uniform(1.5, 2.5))

    # Try to find and click the challenge element
    clicked = False
    for attempt in range(3):
        try:
            result = await conn.send(
                "Runtime.evaluate",
                {"expression": CLOUDFLARE_FIND_CHALLENGE_JS},
            )
            import json
            data = json.loads(result.get("result", {}).get("value", "{}"))

            if data.get("found"):
                x = data["x"]
                y = data["y"]
                logger.info(
                    "Found Cloudflare element '%s' at (%d, %d) — touch-clicking",
                    data["found"], x, y,
                )

                # Touch tap (more natural than mouse click)
                await _touch_tap(conn, x, y)
                clicked = True
                break

        except Exception as exc:
            logger.debug("Cloudflare find attempt %d failed: %s", attempt + 1, exc)

        await asyncio.sleep(1.0)

    if not clicked:
        logger.warning("Could not find Cloudflare challenge element to click")

    # Wait for challenge to resolve (whether we clicked or not)
    return await _wait_for_resolution(conn, timeout=timeout)


async def _touch_tap(conn, x: int, y: int) -> None:
    """Simulate a touchscreen tap at (x, y) via CDP Input.dispatchTouchEvent."""
    touch_point = {"x": x, "y": y, "id": 1}

    # Human-like: small random offset
    touch_point["x"] += random.randint(-2, 2)
    touch_point["y"] += random.randint(-2, 2)

    await conn.send("Input.dispatchTouchEvent", {
        "type": "touchStart",
        "touchPoints": [touch_point],
    })

    # Hold for human-like duration
    await asyncio.sleep(random.uniform(0.05, 0.15))

    await conn.send("Input.dispatchTouchEvent", {
        "type": "touchEnd",
        "touchPoints": [],
    })

    # Small pause after tap
    await asyncio.sleep(random.uniform(0.1, 0.3))


async def _wait_for_resolution(conn, timeout: float = 20.0) -> bool:
    """Wait for Cloudflare challenge to resolve after clicking."""
    elapsed = 0.0
    interval = 1.0

    while elapsed < timeout:
        try:
            result = await conn.send(
                "Runtime.evaluate",
                {"expression": "document.title"},
            )
            title = result.get("result", {}).get("value", "")

            still_challenge = any(p in title for p in CLOUDFLARE_PATTERNS)
            if not still_challenge and len(title) > 3:
                logger.info("Cloudflare resolved after %.1fs", elapsed)
                return True

        except Exception:
            logger.warning("Error while polling Cloudflare challenge status", exc_info=True)

        await asyncio.sleep(interval)
        elapsed += interval

    logger.warning("Cloudflare not resolved after %.0fs", timeout)
    return False


# -- Chrome launch args ----------------------------------------------------

STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
]
