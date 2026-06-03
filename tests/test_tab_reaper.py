"""Tests for tab_reaper — idle reap, hard cap, white-screen refresh.

Mocks the backend's tabs() / close_tab() / switch_tab() / evaluate() /
reload() so we never touch a real browser. Covers:

- Activity tracker (record + GC + idle_seconds)
- sweep_stale_tabs (idle reap, anchored exemption, active exemption,
  pinned-URL exemption)
- enforce_tab_cap (count cap with anchored exemption + oldest-first)
- White-screen JS contract (the helper returns a sensible dict)
- detect_white_screen + refresh_white_screens (per-tick dedup, active-
  tab-checked-last, system tabs skipped, reload preferred over goto)
- run_tab_health_cycle order + summary shape
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from lazyclaw.browser import tab_reaper


def _reset_state():
    """Clear the module's activity tracker between tests."""
    tab_reaper._seen_first.clear()
    tab_reaper._seen_active_last.clear()
    tab_reaper._refreshed_this_tick.clear()


@pytest.fixture(autouse=True)
def _isolate_state():
    _reset_state()
    yield
    _reset_state()


# ── Fake backend + tab objects ──────────────────────────────────────


@dataclass
class _FakeTab:
    id: str
    url: str
    active: bool = False
    title: str = ""


class _FakeBackend:
    def __init__(self, tabs: list[_FakeTab], *, eval_results=None):
        self._tabs = tabs
        self.closed: list[str] = []
        self.switched: list[tuple[str, bool]] = []
        self.reloaded: int = 0
        self.evaluated: list[str] = []
        self._eval_results = eval_results or {}
        self._current_idx = next(
            (i for i, t in enumerate(tabs) if t.active), 0
        )

    async def tabs(self):
        return list(self._tabs)

    async def close_tab(self, tab_id: str):
        self.closed.append(tab_id)
        self._tabs = [t for t in self._tabs if t.id != tab_id]

    async def switch_tab(self, tab_id: str, *, focus: bool = True):
        self.switched.append((tab_id, focus))
        for i, t in enumerate(self._tabs):
            t.active = t.id == tab_id
            if t.active:
                self._current_idx = i

    async def reload(self):
        self.reloaded += 1

    async def evaluate(self, js: str):
        self.evaluated.append(js[:50])
        active = (
            self._tabs[self._current_idx]
            if 0 <= self._current_idx < len(self._tabs)
            else None
        )
        if active and active.id in self._eval_results:
            return self._eval_results[active.id]
        # Default: non-blank, ready
        return {
            "ready": True, "blank": False, "text_len": 5000,
            "children": 30, "interactive": 50, "images": 20,
            "url": active.url if active else "",
        }

    async def current_url(self):
        if 0 <= self._current_idx < len(self._tabs):
            return self._tabs[self._current_idx].url
        return ""


# ── Activity tracker ────────────────────────────────────────────────


def test_record_tab_observation_first_sight():
    tabs = [_FakeTab(id="t1", url="https://a.com", active=True)]
    tab_reaper.record_tab_observation(tabs)
    assert "t1" in tab_reaper._seen_first
    assert "t1" in tab_reaper._seen_active_last


def test_record_tab_observation_inactive_not_in_active_last():
    tabs = [_FakeTab(id="t1", url="https://a.com", active=False)]
    tab_reaper.record_tab_observation(tabs)
    assert "t1" in tab_reaper._seen_first
    assert "t1" not in tab_reaper._seen_active_last


def test_record_tab_observation_gc_closed():
    """Tabs no longer in scan get GC'd from the tracker."""
    tab_reaper.record_tab_observation([
        _FakeTab(id="t1", url="https://a.com", active=True),
        _FakeTab(id="t2", url="https://b.com"),
    ])
    assert "t1" in tab_reaper._seen_first
    assert "t2" in tab_reaper._seen_first
    # t2 closed externally
    tab_reaper.record_tab_observation([
        _FakeTab(id="t1", url="https://a.com", active=True),
    ])
    assert "t1" in tab_reaper._seen_first
    assert "t2" not in tab_reaper._seen_first


def test_tab_idle_seconds_unknown_returns_zero():
    assert tab_reaper.tab_idle_seconds("never-seen-id") == 0.0


def test_tab_idle_seconds_just_first_seen():
    """A tab seen but never active reports idle from first-sight."""
    now = time.monotonic()
    tab_reaper._seen_first["t1"] = now - 5.0
    assert tab_reaper.tab_idle_seconds("t1") >= 4.9


def test_tab_idle_seconds_active_more_recent():
    """When both timestamps exist, active_last wins (more recent)."""
    now = time.monotonic()
    tab_reaper._seen_first["t1"] = now - 100.0
    tab_reaper._seen_active_last["t1"] = now - 2.0
    assert tab_reaper.tab_idle_seconds("t1") < 5.0


# ── _is_pinned_url ──────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "chrome://newtab",
    "chrome-extension://abc/options.html",
    "brave://settings",
    "devtools://devtools/bundled/inspector.html",
    "edge://about",
    "about:blank",
    "",
    None,
])
def test_is_pinned_url_protects_system_tabs(url):
    assert tab_reaper._is_pinned_url(url) is True


def test_is_pinned_url_passes_real_urls():
    assert tab_reaper._is_pinned_url("https://upwork.com") is False
    assert tab_reaper._is_pinned_url("http://localhost:3000") is False


# ── _normalize_host ─────────────────────────────────────────────────


@pytest.mark.parametrize("url,expected", [
    ("https://www.upwork.com/ab/messages", "upwork.com"),
    ("https://UPWORK.com/foo", "upwork.com"),
    ("https://api.upwork.com", "api.upwork.com"),
    ("", ""),
    ("not-a-url", ""),
])
def test_normalize_host(url, expected):
    assert tab_reaper._normalize_host(url) == expected


# ── sweep_stale_tabs ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_stale_tabs_closes_idle():
    """Tabs idle longer than threshold get closed."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="idle", url="https://b.com"),
    ])
    # First observation
    await backend.tabs()
    tab_reaper.record_tab_observation(await backend.tabs())
    # Force "idle" to look old
    tab_reaper._seen_first["idle"] = time.monotonic() - 700.0

    closed = await tab_reaper.sweep_stale_tabs(
        backend, idle_seconds=600.0,
    )
    assert closed == 1
    assert backend.closed == ["idle"]


@pytest.mark.asyncio
async def test_sweep_stale_tabs_preserves_active():
    """Active tab is never closed even if 'idle'."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
    ])
    tab_reaper._seen_first["active"] = time.monotonic() - 9999.0
    closed = await tab_reaper.sweep_stale_tabs(backend, idle_seconds=10.0)
    assert closed == 0
    assert backend.closed == []


@pytest.mark.asyncio
async def test_sweep_stale_tabs_preserves_anchored():
    """Tabs on anchored hosts (watcher targets) are exempt."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://other.com", active=True),
        _FakeTab(id="watcher_tab", url="https://upwork.com/ab/messages"),
    ])
    tab_reaper._seen_first["watcher_tab"] = time.monotonic() - 9999.0
    closed = await tab_reaper.sweep_stale_tabs(
        backend,
        idle_seconds=10.0,
        anchored_hosts=frozenset({"upwork.com"}),
    )
    assert closed == 0
    assert backend.closed == []


@pytest.mark.asyncio
async def test_sweep_stale_tabs_anchored_matches_subdomain():
    """Anchored hosts match exact only — subdomain check via _normalize_host."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://x.com", active=True),
        _FakeTab(id="api_tab", url="https://api.upwork.com/foo"),
    ])
    tab_reaper._seen_first["api_tab"] = time.monotonic() - 9999.0
    # anchored=upwork.com — api.upwork.com normalizes to api.upwork.com,
    # NOT upwork.com, so it WILL be closed (we don't subdomain-match
    # here — anchored is exact hostname). This is intentional: if a
    # watcher targets api.upwork.com, the caller should pass that exact
    # host.
    closed = await tab_reaper.sweep_stale_tabs(
        backend,
        idle_seconds=10.0,
        anchored_hosts=frozenset({"upwork.com"}),
    )
    assert closed == 1
    assert backend.closed == ["api_tab"]


@pytest.mark.asyncio
async def test_sweep_stale_tabs_preserves_pinned():
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="chrome_tab", url="chrome://settings"),
    ])
    tab_reaper._seen_first["chrome_tab"] = time.monotonic() - 9999.0
    closed = await tab_reaper.sweep_stale_tabs(backend, idle_seconds=10.0)
    assert closed == 0


@pytest.mark.asyncio
async def test_sweep_stale_tabs_recently_active_skipped():
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="fresh", url="https://b.com"),
    ])
    tab_reaper.record_tab_observation(await backend.tabs())
    # fresh just seen — should NOT be closed
    closed = await tab_reaper.sweep_stale_tabs(backend, idle_seconds=600.0)
    assert closed == 0


# ── enforce_tab_cap ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_tab_cap_no_op_under_cap():
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="t2", url="https://b.com"),
    ])
    tab_reaper.record_tab_observation(await backend.tabs())
    closed = await tab_reaper.enforce_tab_cap(backend, max_tabs=8)
    assert closed == 0


@pytest.mark.asyncio
async def test_enforce_tab_cap_keep_two():
    """User sets max_tabs=2 — closes oldest non-active, non-anchored."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="t2", url="https://b.com"),
        _FakeTab(id="t3", url="https://c.com"),
        _FakeTab(id="t4", url="https://d.com"),
    ])
    tab_reaper.record_tab_observation(await backend.tabs())
    # t4 oldest, t2 newest of the non-active
    tab_reaper._seen_first["t2"] = time.monotonic() - 10.0
    tab_reaper._seen_first["t3"] = time.monotonic() - 100.0
    tab_reaper._seen_first["t4"] = time.monotonic() - 1000.0

    closed = await tab_reaper.enforce_tab_cap(backend, max_tabs=2)
    # 4 → 2 means 2 close
    assert closed == 2
    # Oldest two (t4, t3) closed; t2 newest of the non-active survives
    assert "t4" in backend.closed
    assert "t3" in backend.closed
    assert "t2" not in backend.closed
    assert "active" not in backend.closed


@pytest.mark.asyncio
async def test_enforce_tab_cap_keeps_anchored_even_over_cap():
    """Anchored watcher tabs survive even if it puts total over cap."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="anchored1", url="https://upwork.com/messages"),
        _FakeTab(id="anchored2", url="https://linkedin.com/messages"),
        _FakeTab(id="closeable", url="https://random.com"),
    ])
    tab_reaper.record_tab_observation(await backend.tabs())
    tab_reaper._seen_first["closeable"] = time.monotonic() - 1000.0

    closed = await tab_reaper.enforce_tab_cap(
        backend, max_tabs=2,
        anchored_hosts=frozenset({"upwork.com", "linkedin.com"}),
    )
    # We're at 4, cap=2, overflow=2. But only 1 candidate is closeable
    # (active + 2 anchored are exempt). So only 1 actually closes.
    assert closed == 1
    assert backend.closed == ["closeable"]


@pytest.mark.asyncio
async def test_enforce_tab_cap_min_floor_one():
    """max_tabs=0 is coerced to 1 (never force-close everything)."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="t2", url="https://b.com"),
    ])
    tab_reaper.record_tab_observation(await backend.tabs())
    tab_reaper._seen_first["t2"] = time.monotonic() - 1000.0
    closed = await tab_reaper.enforce_tab_cap(backend, max_tabs=0)
    # 2 → 1 (cap floored), only t2 closes
    assert closed == 1


# ── detect_white_screen ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_white_screen_blank_signal():
    backend = _FakeBackend(
        [_FakeTab(id="t1", url="https://a.com", active=True)],
        eval_results={"t1": {
            "ready": True, "blank": True, "text_len": 0,
            "children": 1, "interactive": 0, "images": 0,
        }},
    )
    state = await tab_reaper.detect_white_screen(backend)
    assert state["blank"] is True
    assert state["ready"] is True


@pytest.mark.asyncio
async def test_detect_white_screen_non_blank():
    backend = _FakeBackend(
        [_FakeTab(id="t1", url="https://a.com", active=True)],
    )  # default non-blank
    state = await tab_reaper.detect_white_screen(backend)
    assert state["blank"] is False


@pytest.mark.asyncio
async def test_detect_white_screen_handles_eval_failure():
    class _Boom:
        async def evaluate(self, js):
            raise RuntimeError("CDP gone")
    state = await tab_reaper.detect_white_screen(_Boom())
    assert state["blank"] is False
    assert state["ready"] is False


# ── refresh_white_screens ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_white_screens_reloads_blank():
    backend = _FakeBackend(
        [
            _FakeTab(id="active", url="https://a.com", active=True),
            _FakeTab(id="blank", url="https://blank.com"),
        ],
        eval_results={
            "blank": {"ready": True, "blank": True, "text_len": 0,
                      "children": 0, "interactive": 0, "images": 0},
        },
    )
    refreshed = await tab_reaper.refresh_white_screens(backend)
    assert refreshed == 1
    assert backend.reloaded == 1


@pytest.mark.asyncio
async def test_refresh_white_screens_skips_non_blank():
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="loaded", url="https://b.com"),
    ])
    refreshed = await tab_reaper.refresh_white_screens(backend)
    assert refreshed == 0
    assert backend.reloaded == 0


@pytest.mark.asyncio
async def test_refresh_white_screens_per_tick_dedup():
    backend = _FakeBackend(
        [_FakeTab(id="blank", url="https://b.com", active=True)],
        eval_results={"blank": {
            "ready": True, "blank": True, "text_len": 0,
            "children": 0, "interactive": 0, "images": 0,
        }},
    )
    # First pass refreshes
    r1 = await tab_reaper.refresh_white_screens(backend)
    assert r1 == 1
    # Second pass in SAME tick — dedup'd
    r2 = await tab_reaper.refresh_white_screens(backend)
    assert r2 == 0
    # Clear tick → eligible again
    tab_reaper._new_tick()
    r3 = await tab_reaper.refresh_white_screens(backend)
    assert r3 == 1


@pytest.mark.asyncio
async def test_refresh_white_screens_skips_system_tabs():
    backend = _FakeBackend(
        [
            _FakeTab(id="active", url="https://a.com", active=True),
            _FakeTab(id="chrome_tab", url="chrome://newtab"),
        ],
        eval_results={
            "chrome_tab": {"ready": True, "blank": True, "text_len": 0,
                           "children": 0, "interactive": 0, "images": 0},
        },
    )
    refreshed = await tab_reaper.refresh_white_screens(backend)
    assert refreshed == 0
    # chrome:// tab was never even switched to
    chrome_switches = [s for s, _ in backend.switched if s == "chrome_tab"]
    assert chrome_switches == []


@pytest.mark.asyncio
async def test_refresh_white_screens_skips_anchored_cf_tab():
    """A blank-looking anchored CF watcher tab (upwork.com) must NOT be
    reloaded — a Cloudflare interstitial renders nearly blank and a
    reload would restart the challenge on the protected signed-in tab."""
    backend = _FakeBackend(
        [
            _FakeTab(id="active", url="https://a.com", active=True),
            _FakeTab(id="cf_watcher", url="https://www.upwork.com/ab/messages"),
        ],
        eval_results={
            "cf_watcher": {"ready": True, "blank": True, "text_len": 0,
                           "children": 1, "interactive": 0, "images": 0},
        },
    )
    refreshed = await tab_reaper.refresh_white_screens(
        backend, anchored_hosts=frozenset({"upwork.com"}),
    )
    assert refreshed == 0
    assert backend.reloaded == 0
    # The anchored tab must not even be switched-to for the check.
    cf_switches = [s for s, _ in backend.switched if s == "cf_watcher"]
    assert cf_switches == []


@pytest.mark.asyncio
async def test_refresh_white_screens_still_reloads_non_anchored_blank():
    """Anchored exemption is host-scoped — non-anchored blanks still reload."""
    backend = _FakeBackend(
        [
            _FakeTab(id="active", url="https://a.com", active=True),
            _FakeTab(id="cf_watcher", url="https://upwork.com/messages"),
            _FakeTab(id="dead", url="https://random.com"),
        ],
        eval_results={
            "cf_watcher": {"ready": True, "blank": True, "text_len": 0,
                           "children": 0, "interactive": 0, "images": 0},
            "dead": {"ready": True, "blank": True, "text_len": 0,
                     "children": 0, "interactive": 0, "images": 0},
        },
    )
    refreshed = await tab_reaper.refresh_white_screens(
        backend, anchored_hosts=frozenset({"upwork.com"}),
    )
    # Only the non-anchored blank reloads; the anchored CF tab is spared.
    assert refreshed == 1
    assert backend.reloaded == 1


@pytest.mark.asyncio
async def test_run_tab_health_cycle_threads_anchored_to_refresh():
    """End-to-end: anchored_urls reach refresh_white_screens so a blank
    CF watcher tab survives the full health cycle."""
    backend = _FakeBackend(
        [
            _FakeTab(id="active", url="https://a.com", active=True),
            _FakeTab(id="cf_watcher", url="https://www.upwork.com/ab/messages"),
        ],
        eval_results={
            "cf_watcher": {"ready": True, "blank": True, "text_len": 0,
                           "children": 1, "interactive": 0, "images": 0},
        },
    )
    # Keep cf_watcher off the idle/cap reapers too (recently observed).
    tab_reaper.record_tab_observation(await backend.tabs())
    summary = await tab_reaper.run_tab_health_cycle(
        backend, idle_seconds=600.0, max_tabs=8,
        anchored_urls=["https://www.upwork.com/ab/messages"],
    )
    assert summary["blanks_refreshed"] == 0
    assert backend.reloaded == 0
    assert "upwork.com" in summary["anchored_hosts"]


@pytest.mark.asyncio
async def test_refresh_white_screens_uses_focus_false():
    """Switching tabs for the check must NOT steal user focus."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="other", url="https://b.com"),
    ])
    await tab_reaper.refresh_white_screens(backend)
    # Any switch should pass focus=False
    for _tab_id, focus in backend.switched:
        assert focus is False, "white-screen check stole user focus"


# ── run_tab_health_cycle ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_tab_health_cycle_summary_shape():
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
    ])
    summary = await tab_reaper.run_tab_health_cycle(
        backend, idle_seconds=600.0, max_tabs=8,
    )
    assert set(summary.keys()) == {
        "tabs_scanned", "idle_closed", "cap_closed",
        "blanks_refreshed", "anchored_hosts", "anchored_target_ids",
    }
    assert summary["tabs_scanned"] == 1
    assert summary["idle_closed"] == 0


@pytest.mark.asyncio
async def test_run_tab_health_cycle_idle_runs_before_cap():
    """Idle reap runs first — frees the easiest wins before cap kicks in."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
        _FakeTab(id="idle", url="https://b.com"),
        _FakeTab(id="t3", url="https://c.com"),
    ])
    tab_reaper.record_tab_observation(await backend.tabs())
    # idle 10min, t3 just-seen
    tab_reaper._seen_first["idle"] = time.monotonic() - 700.0

    summary = await tab_reaper.run_tab_health_cycle(
        backend, idle_seconds=600.0, max_tabs=10,
    )
    assert summary["idle_closed"] == 1
    assert summary["cap_closed"] == 0


@pytest.mark.asyncio
async def test_run_tab_health_cycle_anchored_urls_normalized():
    """anchored_urls get normalized to hosts in the summary."""
    backend = _FakeBackend([
        _FakeTab(id="active", url="https://a.com", active=True),
    ])
    summary = await tab_reaper.run_tab_health_cycle(
        backend, idle_seconds=600.0, max_tabs=8,
        anchored_urls=[
            "https://www.upwork.com/ab/messages",
            "https://LINKEDIN.com/foo",
        ],
    )
    assert "upwork.com" in summary["anchored_hosts"]
    assert "linkedin.com" in summary["anchored_hosts"]


@pytest.mark.asyncio
async def test_run_tab_health_cycle_skips_refresh_when_disabled():
    backend = _FakeBackend(
        [
            _FakeTab(id="active", url="https://a.com", active=True),
            _FakeTab(id="blank", url="https://b.com"),
        ],
        eval_results={"blank": {
            "ready": True, "blank": True, "text_len": 0,
            "children": 0, "interactive": 0, "images": 0,
        }},
    )
    summary = await tab_reaper.run_tab_health_cycle(
        backend, idle_seconds=600.0, max_tabs=8, refresh_blanks=False,
    )
    assert summary["blanks_refreshed"] == 0
    assert backend.reloaded == 0


@pytest.mark.asyncio
async def test_run_tab_health_cycle_handles_backend_failure():
    """Top-level tabs() failure → empty summary, no crash."""
    class _BoomBackend:
        async def tabs(self):
            raise RuntimeError("CDP gone")
    summary = await tab_reaper.run_tab_health_cycle(
        _BoomBackend(), idle_seconds=600.0, max_tabs=8,
    )
    assert summary["tabs_scanned"] == 0


# ── anchored_target_ids (background-lane owned tabs) ─────────────────


@pytest.mark.asyncio
async def test_sweep_skips_anchored_target_id():
    """A background-owned tab is exempt from idle reap even when it's long
    idle AND on a non-anchored host."""
    bg = _FakeTab(id="BG", url="https://generic-site.com", active=False)
    old = _FakeTab(id="OLD", url="https://stale.com", active=False)
    backend = _FakeBackend([bg, old])
    past = time.monotonic() - 10_000  # both long-idle
    tab_reaper._seen_first["BG"] = past
    tab_reaper._seen_first["OLD"] = past

    closed = await tab_reaper.sweep_stale_tabs(
        backend, idle_seconds=600.0, anchored_target_ids={"BG"},
    )
    assert "BG" not in backend.closed  # owned tab preserved
    assert "OLD" in backend.closed     # the other idle tab still reaped
    assert closed == 1


@pytest.mark.asyncio
async def test_cap_never_closes_anchored_target_id():
    """The cap goes OVER rather than close a background-owned tab."""
    tabs = [_FakeTab(id=f"t{i}", url=f"https://s{i}.com") for i in range(5)]
    tabs[0].active = True
    tabs[2].id = "BG"  # background-owned
    backend = _FakeBackend(tabs)

    await tab_reaper.enforce_tab_cap(
        backend, max_tabs=2, anchored_target_ids={"BG"},
    )
    assert "BG" not in backend.closed


@pytest.mark.asyncio
async def test_refresh_skips_anchored_target_id():
    """A blank background-owned tab is never switched-to or reloaded — that
    would yank a pinned background backend onto a foreign tab."""
    blank = {
        "ready": True, "blank": True, "text_len": 0,
        "children": 0, "interactive": 0, "images": 0, "url": "x",
    }
    bg = _FakeTab(id="BG", url="https://generic.com")
    norm = _FakeTab(id="N", url="https://other.com")
    backend = _FakeBackend([bg, norm], eval_results={"BG": blank, "N": blank})

    refreshed = await tab_reaper.refresh_white_screens(
        backend, anchored_target_ids={"BG"},
    )
    assert all(s[0] != "BG" for s in backend.switched)
    assert refreshed == 1  # only the non-owned blank tab reloaded
