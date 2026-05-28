"""Periodic tab health management: idle reap, count cap, white-screen refresh.

Three jobs the heartbeat daemon calls every ~5 min:

1. **Time-based idle reap** — tabs unfocused for ``idle_tab_close_seconds``
   (default 10 min) get closed, regardless of total tab count. Tabs an
   active watcher targets are exempt (the watcher needs them open to
   poll). The currently-active tab is exempt. System tabs (chrome://,
   brave://, devtools://) are exempt.

2. **Hard tab cap** — enforce ``max_open_tabs`` even when no navigation
   is happening. The existing per-goto sweep in
   ``browser_actions/navigation.py`` only fires after the user / agent
   navigates somewhere. The heartbeat path catches the case where tabs
   accumulate from popups, OAuth flows, or MCP-bundled side-tabs that
   open without going through our ``goto`` skill.

3. **White-screen detection + refresh** — when a tab renders a blank
   body (page failed to load, JS crashed, session expired silently),
   auto-reload it. Detection runs entirely in the page via
   ``Runtime.evaluate`` — zero LLM cost. Reload only fires when
   ``document.readyState === 'complete'`` so we don't refresh during
   normal loading.

The activity tracker (``_seen_first`` / ``_seen_active_last``) is
module-local in-memory. Brave's CDP ``/json/list`` returns each tab's
``id`` (the WebSocket debugger URL hash) which is stable while the tab
is alive, so the dict can use it as a key. On every scan we GC entries
for tabs that no longer appear in the live list.

Safety invariants — these must hold even when the user is mid-task in
their other session:
  - Active (focused) tab is NEVER closed
  - Any tab whose URL host matches an active watcher's URL is NEVER closed
  - System / extension tabs (chrome://, etc.) are NEVER closed
  - White-screen refresh only fires once per tab per heartbeat tick
    (avoid reload loops when the page legitimately renders blank)
"""

from __future__ import annotations

import logging
import time
from typing import Iterable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Module-state activity tracker. Process-local — restarts forget everything,
# which is fine: on first observation we treat the tab as "just seen".
_seen_first: dict[str, float] = {}
_seen_active_last: dict[str, float] = {}

# Tab IDs we've refreshed THIS tick — prevents reload loops when the
# page is legitimately blank (e.g. a deliberately-empty 200 OK).
_refreshed_this_tick: set[str] = set()


# System / extension URLs that must never be auto-closed or auto-refreshed.
# Mirrors ``browser_actions/navigation.py::_PINNED_URL_PREFIXES`` so the
# two reapers agree on what's untouchable.
_PINNED_URL_PREFIXES = (
    "chrome://",
    "chrome-extension://",
    "brave://",
    "devtools://",
    "edge://",
)


def _is_pinned_url(url: str | None) -> bool:
    if not url:
        return True
    if url == "about:blank":
        return True
    return url.startswith(_PINNED_URL_PREFIXES)


def _normalize_host(url: str | None) -> str:
    """Bare lowercase hostname, ``www.`` stripped. ``""`` on parse failure."""
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def record_tab_observation(tabs: Iterable) -> None:
    """Update the activity tracker from a fresh scan of open tabs.

    For each tab seen:
      - First sight → record ``_seen_first[tab.id] = now``
      - If active → bump ``_seen_active_last[tab.id] = now``

    Then GC entries for tab IDs not in the current scan (the tabs were
    closed externally — Brave, user clicked X, OAuth popup self-closed).
    """
    now = time.monotonic()
    tab_list = list(tabs)
    open_ids: set[str] = set()
    for tab in tab_list:
        tid = getattr(tab, "id", None)
        if not tid:
            continue
        open_ids.add(tid)
        _seen_first.setdefault(tid, now)
        if getattr(tab, "active", False):
            _seen_active_last[tid] = now

    # GC entries for closed tabs
    for stale_id in list(_seen_first.keys()):
        if stale_id not in open_ids:
            _seen_first.pop(stale_id, None)
            _seen_active_last.pop(stale_id, None)
    for stale_id in list(_seen_active_last.keys()):
        if stale_id not in open_ids:
            _seen_active_last.pop(stale_id, None)


def tab_idle_seconds(tab_id: str) -> float:
    """Seconds since this tab was last observed active.

    Falls back to "seconds since first observed" when the tab was never
    seen active. Returns 0 when the tab is unknown (just-opened — give
    it the benefit of the doubt).
    """
    if tab_id not in _seen_first:
        return 0.0
    now = time.monotonic()
    last = _seen_active_last.get(tab_id, _seen_first[tab_id])
    return max(0.0, now - last)


def _new_tick() -> None:
    """Clear per-tick state. Call once at the start of each heartbeat sweep."""
    _refreshed_this_tick.clear()


# ── Idle reap ────────────────────────────────────────────────────────


async def sweep_stale_tabs(
    backend,
    *,
    idle_seconds: float = 600.0,
    anchored_hosts: frozenset[str] | set[str] | None = None,
) -> int:
    """Close tabs unfocused longer than ``idle_seconds``.

    Returns the number of tabs actually closed.

    Skips:
      - The active (focused) tab — user is using it
      - System / extension tabs (chrome://, brave://, etc.)
      - Tabs whose URL host is in ``anchored_hosts`` (a watcher needs them)

    This is the time-based complement to ``sweep_idle_tabs`` in
    navigation.py (which is count-based, fires on goto). Both can run
    safely — they target overlapping but distinct conditions.
    """
    try:
        tabs = await backend.tabs()
    except Exception:
        logger.debug("sweep_stale_tabs: backend.tabs() failed", exc_info=True)
        return 0

    record_tab_observation(tabs)
    anchored = anchored_hosts or frozenset()

    closed = 0
    for tab in tabs:
        if getattr(tab, "active", False):
            continue
        url = getattr(tab, "url", "") or ""
        if _is_pinned_url(url):
            continue
        if _normalize_host(url) in anchored:
            continue
        if tab_idle_seconds(tab.id) < idle_seconds:
            continue
        try:
            await backend.close_tab(tab.id)
            _seen_first.pop(tab.id, None)
            _seen_active_last.pop(tab.id, None)
            closed += 1
            logger.info(
                "sweep_stale_tabs: closed idle tab id=%s url=%s",
                tab.id[:24], url[:80],
            )
        except Exception:
            logger.debug(
                "sweep_stale_tabs: close failed for %s", tab.id, exc_info=True,
            )
    return closed


# ── Hard cap enforcement ─────────────────────────────────────────────


async def enforce_tab_cap(
    backend,
    *,
    max_tabs: int,
    anchored_hosts: frozenset[str] | set[str] | None = None,
) -> int:
    """Close oldest non-active, non-anchored tabs until total <= max_tabs.

    Differs from ``navigation.sweep_idle_tabs``: this version respects
    ``anchored_hosts`` (tabs the watcher needs stay open even if they
    push total over the cap — better to be over-cap than break a paid
    monitoring contract).

    Returns the number closed.
    """
    if max_tabs < 1:
        max_tabs = 1
    try:
        tabs = await backend.tabs()
    except Exception:
        logger.debug("enforce_tab_cap: backend.tabs() failed", exc_info=True)
        return 0

    record_tab_observation(tabs)
    if len(tabs) <= max_tabs:
        return 0

    anchored = anchored_hosts or frozenset()
    overflow = len(tabs) - max_tabs

    # Eligible to close: non-active, non-anchored, non-system. Ordered
    # OLDEST FIRST (largest idle_seconds). Close from the top until
    # overflow drops to zero.
    candidates = [
        t for t in tabs
        if not getattr(t, "active", False)
        and not _is_pinned_url(getattr(t, "url", ""))
        and _normalize_host(getattr(t, "url", "")) not in anchored
    ]
    candidates.sort(key=lambda t: tab_idle_seconds(t.id), reverse=True)

    to_close = candidates[:overflow]
    closed = 0
    for tab in to_close:
        try:
            await backend.close_tab(tab.id)
            _seen_first.pop(tab.id, None)
            _seen_active_last.pop(tab.id, None)
            closed += 1
        except Exception:
            logger.debug(
                "enforce_tab_cap: close failed for %s", tab.id, exc_info=True,
            )

    if closed:
        logger.info(
            "enforce_tab_cap: closed %d tab(s), cap=%d (was %d, %d anchored)",
            closed, max_tabs, len(tabs), len([
                t for t in tabs
                if _normalize_host(getattr(t, "url", "")) in anchored
            ]),
        )
    return closed


# ── White-screen detection + refresh ─────────────────────────────────


# Runs entirely on the page — no LLM. Returns a small dict the caller
# inspects. Heuristic:
#   "blank" = readyState complete + body has <10 chars of visible text
#             + <3 body children + zero interactive elements
# The interactive-element gate is what avoids false positives on
# legitimately-minimalist pages (a login form has zero text but lots of
# inputs, so blank=False).
_WHITE_SCREEN_JS = """
(() => {
    try {
        if (document.readyState !== 'complete') {
            return {ready: false, blank: false, reason: 'loading'};
        }
        const body = document.body;
        if (!body) return {ready: true, blank: true, reason: 'no body'};
        const text = (body.innerText || '').trim();
        const childCount = body.children.length;
        const interactiveCount = document.querySelectorAll(
            'a, button, input, select, textarea, form, video, canvas'
        ).length;
        const imgCount = document.querySelectorAll('img').length;
        const blank = text.length < 10
            && childCount < 3
            && interactiveCount < 1
            && imgCount < 1;
        return {
            ready: true,
            blank: blank,
            text_len: text.length,
            children: childCount,
            interactive: interactiveCount,
            images: imgCount,
            url: document.location.href,
        };
    } catch (e) {
        return {ready: false, blank: false, error: String(e)};
    }
})()
"""


async def detect_white_screen(backend) -> dict:
    """Run the white-screen heuristic on the current tab. Returns the
    result dict (see ``_WHITE_SCREEN_JS``). Empty / on error returns
    ``{ready: False, blank: False}`` so callers don't refresh on a
    transient eval failure.
    """
    try:
        result = await backend.evaluate(_WHITE_SCREEN_JS)
    except Exception as exc:
        logger.debug("detect_white_screen evaluate failed: %s", exc)
        return {"ready": False, "blank": False, "error": str(exc)}
    if not isinstance(result, dict):
        return {"ready": False, "blank": False}
    return result


async def refresh_white_screens(
    backend,
    *,
    max_checks: int = 8,
    anchored_hosts: frozenset[str] | set[str] | None = None,
) -> int:
    """Scan up to ``max_checks`` tabs for white-screen state and reload
    any that match. Returns the number reloaded.

    Per-tick deduplication: a tab refreshed in this tick won't be
    refreshed again. Prevents reload loops on pages that legitimately
    render blank for >1 tick (e.g. a 200-OK empty page or an SPA mid-
    redirect).

    The currently-active tab is checked LAST so we don't switch focus
    in the middle of a user action.

    Tabs whose normalized host is in ``anchored_hosts`` are SKIPPED:
    these are the user's signed-in, Cloudflare-protected watcher tabs
    (upwork.com / linkedin.com / user live_hosts). A CF "Checking your
    browser…" interstitial renders nearly blank and would trip the
    white-screen heuristic — force-reloading it restarts the CF
    challenge on the protected tab and can knock the watcher offline.
    Mirrors the host normalization used by ``sweep_stale_tabs`` /
    ``enforce_tab_cap``.
    """
    try:
        tabs = await backend.tabs()
    except Exception:
        logger.debug("refresh_white_screens: tabs() failed", exc_info=True)
        return 0

    anchored = anchored_hosts or frozenset()

    # Active tab last; system tabs skipped entirely; anchored CF watcher
    # tabs skipped (a CF interstitial looks blank — never reload it).
    ordered = sorted(
        [
            t for t in tabs
            if not _is_pinned_url(getattr(t, "url", ""))
            and _normalize_host(getattr(t, "url", "")) not in anchored
        ],
        key=lambda t: 1 if getattr(t, "active", False) else 0,
    )[:max_checks]

    refreshed = 0
    for tab in ordered:
        if tab.id in _refreshed_this_tick:
            continue
        try:
            await backend.switch_tab(tab.id, focus=False)
        except TypeError:
            # Backend's switch_tab may not accept focus kwarg yet
            try:
                await backend.switch_tab(tab.id)
            except Exception:
                logger.debug(
                    "refresh_white_screens: switch to %s failed",
                    tab.id, exc_info=True,
                )
                continue
        except Exception:
            logger.debug(
                "refresh_white_screens: switch to %s failed",
                tab.id, exc_info=True,
            )
            continue

        state = await detect_white_screen(backend)
        if not state.get("blank") or not state.get("ready"):
            continue

        # Reload via Page.reload — preserves session state better than
        # goto(current_url). Fall back to goto if reload missing.
        reload_fn = getattr(backend, "reload", None)
        try:
            if callable(reload_fn):
                await reload_fn()
            else:
                current = await backend.current_url()
                if current:
                    await backend.goto(current)
            _refreshed_this_tick.add(tab.id)
            refreshed += 1
            logger.info(
                "refresh_white_screens: reloaded white-screen tab id=%s url=%s",
                tab.id[:24],
                (getattr(tab, "url", "") or "")[:80],
            )
        except Exception:
            logger.debug(
                "refresh_white_screens: reload failed for %s",
                tab.id, exc_info=True,
            )

    return refreshed


# ── Unified entry point — heartbeat calls this ──────────────────────


async def run_tab_health_cycle(
    backend,
    *,
    idle_seconds: float = 600.0,
    max_tabs: int = 8,
    anchored_urls: Iterable[str] | None = None,
    refresh_blanks: bool = True,
) -> dict:
    """One full health cycle: reap idle → enforce cap → refresh blanks.

    Returns a summary dict so the caller can log + emit metrics:
        {
            "tabs_scanned": int,
            "idle_closed": int,
            "cap_closed": int,
            "blanks_refreshed": int,
            "anchored_hosts": list[str],
        }

    Order matters:
      1. Idle reap runs first — frees the easiest wins, drops total before
         cap enforcement kicks in.
      2. Cap enforcement runs second — catches anything still over budget
         (e.g. recently-opened popups that aren't idle yet but push count
         over the cap).
      3. White-screen refresh runs last — by now the live tab set is
         minimal, so checks are fast.
    """
    _new_tick()

    anchored_hosts = frozenset(
        _normalize_host(u) for u in (anchored_urls or []) if u
    )

    try:
        tabs = await backend.tabs()
        record_tab_observation(tabs)
        tabs_scanned = len(tabs)
    except Exception:
        logger.debug("run_tab_health_cycle: initial tabs() failed", exc_info=True)
        tabs_scanned = 0

    idle_closed = await sweep_stale_tabs(
        backend, idle_seconds=idle_seconds, anchored_hosts=anchored_hosts,
    )
    cap_closed = await enforce_tab_cap(
        backend, max_tabs=max_tabs, anchored_hosts=anchored_hosts,
    )
    blanks_refreshed = 0
    if refresh_blanks:
        blanks_refreshed = await refresh_white_screens(
            backend, anchored_hosts=anchored_hosts,
        )

    return {
        "tabs_scanned": tabs_scanned,
        "idle_closed": idle_closed,
        "cap_closed": cap_closed,
        "blanks_refreshed": blanks_refreshed,
        "anchored_hosts": sorted(anchored_hosts),
    }
