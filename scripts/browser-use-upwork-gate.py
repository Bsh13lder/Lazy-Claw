"""V3: Upwork inbox behind Cloudflare via the signed-in host Brave.

READ-ONLY: navigates to /ab/messages in a tab WE create, asserts the real
inbox DOM rendered (never HTTP 200 — the current stack fails silently),
closes the tab. Each iteration is a COLD attach (fresh CDP connection).
"""

import asyncio

from lazyclaw.browser.browser_use_backend import BrowserUseBackend

INBOX_URL = "https://www.upwork.com/ab/messages"

# Markers from mcp-upwork/src/upwork_mcp/browser/client.py: challenge = URL
# on challenges.cloudflare.com or body saying "Just a moment"/"Verifying".
CHECK_JS = """
(() => {
  const body = document.body ? document.body.innerText.slice(0, 4000) : '';
  return {
    url: location.href,
    title: document.title,
    challenged: /just a moment|verifying you are human/i.test(body),
    room_links: document.querySelectorAll(
      "a[href*='/ab/messages/rooms/'], [data-test*='room'], [class*='room-list']"
    ).length,
    ready: document.readyState,
  };
})()
"""


async def one_cold_attach(i: int) -> dict:
    backend = BrowserUseBackend(port=9222)
    own_tab = None
    try:
        await backend._ensure_connected()
        own_tab = await backend.new_tab("about:blank")
        await backend.switch_tab(own_tab, focus=False)
        await backend.goto(INBOX_URL)

        # Wait for the SPA to settle: poll up to 45s for inbox DOM
        result = {}
        for _ in range(45):
            await asyncio.sleep(1.0)
            try:
                result = await backend.evaluate(CHECK_JS) or {}
            except Exception:
                continue
            if result.get("challenged"):
                break
            if result.get("room_links", 0) > 0:
                break

        on_upwork = "upwork.com" in result.get("url", "")
        off_challenge = (
            not result.get("challenged")
            and "challenges.cloudflare.com" not in result.get("url", "")
        )
        has_inbox_dom = result.get("room_links", 0) > 0
        return {
            "iter": i,
            "pass": on_upwork and off_challenge and has_inbox_dom,
            "url": result.get("url", "")[:90],
            "title": result.get("title", "")[:60],
            "challenged": result.get("challenged"),
            "room_links": result.get("room_links"),
        }
    except Exception as exc:
        return {"iter": i, "pass": False, "error": str(exc)[:200]}
    finally:
        try:
            if own_tab:
                await backend.close_tab(own_tab)
        finally:
            await backend.close()


async def main() -> None:
    results = []
    for i in range(3):
        r = await one_cold_attach(i)
        results.append(r)
        print(f"  attach {i}: {r}")
        await asyncio.sleep(5)
    passed = sum(1 for r in results if r["pass"])
    print(f"V3 (3 cold attaches): {passed}/3 {'PASS' if passed == 3 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
