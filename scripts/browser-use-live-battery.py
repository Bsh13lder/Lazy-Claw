"""Live verification battery for BrowserUseBackend (V1 + V2).

READ-ONLY against the user's real Brave on :9222. All actions happen in a
tab WE create and close; pre-existing tabs are never touched. No user_id is
bound, so no events reach the production event bus.

Run: .venv/bin/python live_battery.py  (from repo root, host machine)
"""

import asyncio
import statistics
import time
import urllib.parse

from lazyclaw.browser.browser_use_backend import BrowserUseBackend

FORM_HTML = """
<html><body>
  <input id='q' type='text'>
  <button id='go' onclick="
    document.getElementById('echo').textContent =
      'echo:' + document.getElementById('q').value">Go</button>
  <div id='echo'></div>
</body></html>
"""
FORM_URL = "data:text/html," + urllib.parse.quote(FORM_HTML)


async def v1_attach_safety() -> dict:
    backend = BrowserUseBackend(port=9222)
    pre_tabs = None
    own_tab = None
    ops_ok, ops_fail = 0, []
    try:
        await backend._ensure_connected()
        pre_tabs = {t.id for t in await backend.tabs()}
        own_tab = await backend.new_tab("about:blank")
        await backend.switch_tab(own_tab, focus=False)

        ops = [
            ("goto", lambda: backend.goto(FORM_URL)),
            ("current_url", backend.current_url),
            ("title", backend.title),
            ("evaluate", lambda: backend.evaluate("1 + 1")),
            ("wait_for_selector", lambda: backend.wait_for_selector("#q", 3000)),
            ("click", lambda: backend.click("#q")),
            ("type_text", lambda: backend.type_text("#q", "v1")),
            ("press_key", lambda: backend.press_key("tab")),
            ("scroll", lambda: backend.scroll("down", 100)),
            ("hover", lambda: backend.hover("#go")),
            ("find_by_role", lambda: backend.find_element_by_role("Go")),
            ("click_by_role", lambda: backend.click_by_role("Go")),
            ("content", backend.content),
            ("screenshot", backend.screenshot),
            ("console_inject", backend.inject_console_capture),
            ("console_logs", backend.get_console_logs),
            ("tabs", backend.tabs),
            ("evaluate2", lambda: backend.evaluate("document.readyState")),
            ("wait2", lambda: backend.wait_for_selector("#echo", 2000)),
            ("url2", backend.current_url),
        ]
        for name, op in ops:
            try:
                await op()
                ops_ok += 1
            except Exception as exc:
                ops_fail.append(f"{name}: {exc}")
    finally:
        try:
            if own_tab:
                await backend.close_tab(own_tab)
        finally:
            await backend.close()

    # Fresh attach: verify pre-existing tabs untouched, our tab gone
    backend2 = BrowserUseBackend(port=9222)
    await backend2._ensure_connected()
    post_tabs = {t.id for t in await backend2.tabs()}
    await backend2.close()

    return {
        "ops_ok": ops_ok,
        "ops_fail": ops_fail,
        "pre_tabs_intact": pre_tabs is not None and pre_tabs <= post_tabs,
        "own_tab_removed": own_tab not in post_tabs,
        "tab_delta": sorted(post_tabs - pre_tabs) if pre_tabs else None,
    }


async def v2_action_reliability(cycles: int = 50) -> dict:
    backend = BrowserUseBackend(port=9222)
    own_tab = None
    ok, failures, latencies = 0, [], []
    try:
        await backend._ensure_connected()
        own_tab = await backend.new_tab(FORM_URL)
        await backend.switch_tab(own_tab, focus=False)
        await backend.wait_for_selector("#q", 5000)

        for i in range(cycles):
            t0 = time.monotonic()
            try:
                text = f"run {i}"
                await backend.evaluate(
                    "document.getElementById('q').value = '';"
                    "document.getElementById('echo').textContent = ''"
                )
                await backend.type_text("#q", text)
                await backend.click("#go")
                echoed = await backend.evaluate(
                    "document.getElementById('echo').textContent"
                )
                if echoed == f"echo:{text}":
                    ok += 1
                else:
                    failures.append(f"cycle {i}: echoed {echoed!r}")
            except Exception as exc:
                failures.append(f"cycle {i}: {exc}")
            latencies.append(time.monotonic() - t0)
    finally:
        try:
            if own_tab:
                await backend.close_tab(own_tab)
        finally:
            await backend.close()

    lat = sorted(latencies)
    return {
        "ok": ok,
        "cycles": cycles,
        "failures": failures[:5],
        "p50_s": round(statistics.median(lat), 2) if lat else None,
        "p95_s": round(lat[int(len(lat) * 0.95) - 1], 2) if lat else None,
    }


async def main() -> None:
    print("=== V1 attach safety ===")
    v1 = await v1_attach_safety()
    for k, v in v1.items():
        print(f"  {k}: {v}")

    print("=== V2 action reliability (50 cycles) ===")
    v2 = await v2_action_reliability(50)
    for k, v in v2.items():
        print(f"  {k}: {v}")

    v1_pass = not v1["ops_fail"] and v1["pre_tabs_intact"] and v1["own_tab_removed"]
    v2_pass = v2["ok"] >= v2["cycles"] - 1
    print(f"V1: {'PASS' if v1_pass else 'FAIL'} | V2: {'PASS' if v2_pass else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
