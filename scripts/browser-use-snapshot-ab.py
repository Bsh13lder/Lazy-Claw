"""A/B measurement: our SnapshotManager vs vendored browser-use's DomService.

Answers one question with numbers: would browser-use 0.13.7's AX-tree-based
DomService/DOMTreeSerializer (``lazyclaw/_vendor/browser_use/dom/``) be a
cheaper or better source of page state than our own
``lazyclaw/browser/snapshot.py::SnapshotManager``?

- "Ours"   = ``SnapshotManager.take_snapshot()`` + the SAME auto-compact
  logic production uses (``skills/builtin/browser_actions/capture.py``):
  ``format_snapshot_compact()`` when element_count > 30, else
  ``format_snapshot()`` — both with default args (no task_hint).
- "Theirs" = ``DomService.get_serialized_dom_tree()`` ->
  ``SerializedDOMState.llm_representation()``, via a TEST-SCOPE adapter
  (``scripts/browser_use_dom_testscope_adapter.py::TestScopeBrowserSession``)
  that implements only the browser_session surface DomService actually
  touches with ``cross_origin_iframes=False`` — see that module's docstring
  for exactly what is and isn't implemented, and why.

Safety (read before running):
- Attach-ONLY to the host Brave on CDP :9222. Every tab used here is
  created by this script and closed in a ``finally`` block. Pre-existing
  tabs (including a live Upwork tab) are never touched, navigated, or
  closed.
- NEVER navigates to upwork.com or linkedin.com — a rollout gate is
  running against Upwork concurrently in the same browser.
- Does not touch ``./data`` (no DB access at all) and makes no edits to
  any production module.

Run (host machine, repo root):
    .venv/bin/python scripts/browser-use-snapshot-ab.py
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for p in (str(SCRIPT_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_use_dom_testscope_adapter import TestScopeBrowserSession  # noqa: E402

from lazyclaw._vendor import ensure_vendor_path  # noqa: E402
from lazyclaw.browser.browser_use_backend import BrowserUseBackend  # noqa: E402
from lazyclaw.browser.snapshot import SnapshotManager  # noqa: E402

ensure_vendor_path()
from browser_use.dom.service import DomService  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("snapshot_ab")
# Silence cdp_use's default INFO-level "connecting/connected" chatter and the
# vendored DomService's own debug logging so the table output stays readable.
logging.getLogger("cdp_use.client").setLevel(logging.WARNING)
logging.getLogger("browser_use").setLevel(logging.WARNING)

RUNS_PER_PAGE = 3
SETTLE_S = 1.5  # let network-idle-ish JS-heavy pages finish rendering before capture
COMPACT_THRESHOLD = 30  # mirrors skills/builtin/browser_actions/capture.py::action_snapshot

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


def discover_himap_url() -> str | None:
    """``docker port himap_web`` -> http://localhost:<host-port>, or None."""
    try:
        out = subprocess.run(
            ["docker", "port", "himap_web"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception as exc:
        logger.warning("docker port himap_web failed: %s", exc)
        return None
    # Typical line: "8000/tcp -> 127.0.0.1:8001"
    for line in out.splitlines():
        if "->" not in line:
            continue
        host_part = line.split("->")[-1].strip()
        port = host_part.rsplit(":", 1)[-1]
        if port.isdigit():
            return f"http://localhost:{port}"
    logger.warning("Could not parse `docker port himap_web` output: %r", out)
    return None


@dataclass(frozen=True)
class CaptureResult:
    label: str
    chars: int
    tokens_est: int
    element_count: int
    latencies_s: tuple[float, ...]
    text_sample: str

    @property
    def p50_s(self) -> float:
        return statistics.median(self.latencies_s)


async def capture_ours(backend: BrowserUseBackend) -> CaptureResult:
    mgr = SnapshotManager()
    latencies = []
    snapshot = None
    for _ in range(RUNS_PER_PAGE):
        t0 = time.monotonic()
        snapshot = await mgr.take_snapshot(backend)
        latencies.append(time.monotonic() - t0)

    assert snapshot is not None
    use_compact = snapshot.element_count > COMPACT_THRESHOLD
    text = (
        mgr.format_snapshot_compact(snapshot)
        if use_compact
        else mgr.format_snapshot(snapshot)
    )
    return CaptureResult(
        label="ours (SnapshotManager)",
        chars=len(text),
        tokens_est=len(text) // 4,
        element_count=snapshot.element_count,
        latencies_s=tuple(latencies),
        text_sample=text[:600],
    )


async def capture_theirs(client, target_id: str) -> CaptureResult:
    session = TestScopeBrowserSession(client, target_id)
    dom_service = DomService(
        session,
        logger=logging.getLogger("dom_service_ab"),
        cross_origin_iframes=False,
    )
    latencies = []
    state = None
    for _ in range(RUNS_PER_PAGE):
        t0 = time.monotonic()
        state, _root, _timing = await dom_service.get_serialized_dom_tree()
        latencies.append(time.monotonic() - t0)

    assert state is not None
    text = state.llm_representation()
    return CaptureResult(
        label="theirs (DomService)",
        chars=len(text),
        tokens_est=len(text) // 4,
        element_count=len(state.selector_map),
        latencies_s=tuple(latencies),
        text_sample=text[:600],
    )


async def run_page(label: str, url: str) -> dict:
    """Run both captures for one page in its own tab; always cleans up."""
    backend = BrowserUseBackend(port=9222)
    own_tab: str | None = None
    result: dict = {"label": label, "url": url}
    try:
        await backend._ensure_connected()
        own_tab = await backend.new_tab("about:blank")
        await backend.switch_tab(own_tab, focus=False)
        await backend.goto(url)
        await asyncio.sleep(SETTLE_S)

        try:
            result["ours"] = await capture_ours(backend)
        except Exception as exc:
            logger.exception("ours capture failed for %s", label)
            result["ours_error"] = repr(exc)

        # Reuses the CDPClient websocket `backend` already has attached to
        # this exact tab, so both codepaths read the identical DOM state
        # without a second browser connection. DomService attaches its OWN
        # CDP session (a separate `Target.attachToTarget` sessionId) on top
        # of it via TestScopeBrowserSession — CDP supports multiple
        # concurrently attached sessions per target, same as multiple
        # DevTools panels inspecting one tab.
        try:
            result["theirs"] = await capture_theirs(backend._client, own_tab)
        except Exception as exc:
            logger.exception("theirs capture failed for %s", label)
            result["theirs_error"] = repr(exc)
    finally:
        try:
            if own_tab:
                await backend.close_tab(own_tab)
        finally:
            await backend.close()
    return result


def _fmt_result(r: CaptureResult | None, err: str | None) -> str:
    if err:
        return f"ERROR: {err}"
    if r is None:
        return "n/a"
    return (
        f"{r.chars:>7} chars | ~{r.tokens_est:>5} tok | "
        f"{r.element_count:>4} elems | p50 {r.p50_s * 1000:>7.1f} ms "
        f"(runs: {[round(x * 1000) for x in r.latencies_s]})"
    )


async def main() -> None:
    himap_url = discover_himap_url()
    if not himap_url:
        print("!! Could not discover himap_web port via `docker port himap_web` — "
              "skipping that test page.")

    pages: list[tuple[str, str]] = [("data: form", FORM_URL)]
    if himap_url:
        pages.append(("himap_web (login)", himap_url))
    pages.append(("example.com", "https://example.com"))
    pages.append(("news.ycombinator.com", "https://news.ycombinator.com"))

    results = []
    for label, url in pages:
        print(f"\n=== {label} ({url}) ===")
        r = await run_page(label, url)
        results.append(r)
        print(f"  ours:   {_fmt_result(r.get('ours'), r.get('ours_error'))}")
        print(f"  theirs: {_fmt_result(r.get('theirs'), r.get('theirs_error'))}")

    print("\n\n=== SUMMARY TABLE ===")
    header = (
        f"{'page':<22} {'src':<8} {'chars':>8} {'~tok':>7} {'elems':>6} "
        f"{'p50 ms':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        for key, src in (("ours", "ours"), ("theirs", "theirs")):
            cap: CaptureResult | None = r.get(key)
            if cap is None:
                print(f"{r['label']:<22} {src:<8} {'ERROR':>8}")
                continue
            print(
                f"{r['label']:<22} {src:<8} {cap.chars:>8} {cap.tokens_est:>7} "
                f"{cap.element_count:>6} {cap.p50_s * 1000:>8.1f}"
            )

    print("\n=== TEXT SAMPLES (first 600 chars, for spot-check) ===")
    for r in results:
        print(f"\n--- {r['label']} : ours ---")
        ours = r.get("ours")
        print(ours.text_sample if ours else r.get("ours_error", "n/a"))
        print(f"\n--- {r['label']} : theirs ---")
        theirs = r.get("theirs")
        print(theirs.text_sample if theirs else r.get("theirs_error", "n/a"))


if __name__ == "__main__":
    asyncio.run(main())
