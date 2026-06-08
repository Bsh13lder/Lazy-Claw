"""Tests for the watcher's own parked tab (two-lane browser model, 2026-06-02).

A live (``passive=True``) watcher must drive its OWN dedicated tab — selected
by ``anchor_target_id``, created once at the watched URL — and must NEVER grab
the user's foreground tab by host-match. The headless (``passive=False``) path
keeps its original navigate behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lazyclaw.browser import owned_tabs
from lazyclaw.browser.watcher import check_watcher


@pytest.fixture(autouse=True)
def _clean():
    owned_tabs._registry.clear()
    yield
    owned_tabs._registry.clear()


class _FakeBackend:
    """CDP backend stand-in recording new_tab / switch_tab calls."""

    def __init__(self, *, results, tabs_list, url="about:blank"):
        self._results = list(results)
        self._tabs = list(tabs_list)
        self._url = url
        self.new_tab_calls: list[str] = []
        self.new_tab_bg: list[bool] = []
        self.switch_calls: list[tuple[str, bool]] = []
        self._next = 100

    async def current_url(self) -> str:
        return self._url

    async def tabs(self):
        return list(self._tabs)

    async def new_tab(self, url: str = "about:blank", *, background: bool = False) -> str:
        self.new_tab_calls.append(url)
        self.new_tab_bg.append(background)
        tid = f"NEW{self._next}"
        self._next += 1
        self._tabs.append(SimpleNamespace(id=tid, url=url))
        return tid

    async def switch_tab(self, tab_id: str, *, focus: bool = True) -> None:
        self.switch_calls.append((tab_id, focus))

    async def evaluate(self, js: str):
        if "location.pathname" in js:  # nav-sidecar — keep out of the queue
            return None
        return self._results.pop(0)

    async def goto(self, *a, **k):  # pragma: no cover - passive never navigates
        return None


def _ctx(**over) -> dict:
    ctx = {
        "url": "https://www.upwork.com/ab/messages/rooms/",
        "page_type": "auto",
        "custom_js": None,
        "check_interval": 300,
        "last_value": None,
        "last_check": None,
    }
    ctx.update(over)
    return ctx


@pytest.mark.asyncio
async def test_first_run_creates_own_parked_tab():
    b = _FakeBackend(
        results=[{"unread": 2}],
        tabs_list=[SimpleNamespace(id="USERTAB", url="https://gmail.com")],
    )
    ctx = _ctx()
    _changed, _notif, new_ctx = await check_watcher(
        b, ctx, passive=True, user_id="u1", job_id="job9",
    )
    # Created its OWN tab at the watched URL, in the BACKGROUND (no screen
    # jump), attached with focus=False.
    assert b.new_tab_calls == [ctx["url"]]
    assert b.new_tab_bg == [True]
    anchor = new_ctx["anchor_target_id"]
    assert anchor.startswith("NEW")
    assert b.switch_calls[-1] == (anchor, False)
    # Never touched the user's tab.
    assert all(c[0] != "USERTAB" for c in b.switch_calls)
    # Registered for foreground-exclusion + reaper-anchoring.
    assert anchor in owned_tabs.all_owned_target_ids("u1")
    assert owned_tabs.get_owned("u1", "watch:job9") == anchor


@pytest.mark.asyncio
async def test_passive_never_grabs_foreground_tab_on_same_host():
    """THE regression: a tab already open on the watched host (the user's
    foreground tab) must NOT be hijacked — the watcher makes its own."""
    fg = SimpleNamespace(
        id="FG_UPWORK", url="https://www.upwork.com/nx/find-work/",
    )
    b = _FakeBackend(results=[{"x": 1}], tabs_list=[fg])
    ctx = _ctx(url="https://www.upwork.com/ab/messages/rooms/")
    _changed, _notif, new_ctx = await check_watcher(
        b, ctx, passive=True, user_id="u1", job_id="job9",
    )
    assert all(c[0] != "FG_UPWORK" for c in b.switch_calls)
    assert b.new_tab_calls == [ctx["url"]]
    assert new_ctx["anchor_target_id"].startswith("NEW")


@pytest.mark.asyncio
async def test_reuses_existing_parked_tab_by_id():
    anchor = SimpleNamespace(id="ANCHOR1", url="https://www.upwork.com/x")
    b = _FakeBackend(results=[{"unread": 2}], tabs_list=[anchor])
    ctx = _ctx(anchor_target_id="ANCHOR1", last_value='{"unread": 1}')
    _changed, _notif, new_ctx = await check_watcher(
        b, ctx, passive=True, user_id="u1", job_id="job9",
    )
    assert b.new_tab_calls == []  # reused, no new tab
    assert b.switch_calls == [("ANCHOR1", False)]
    assert new_ctx["anchor_target_id"] == "ANCHOR1"


@pytest.mark.asyncio
async def test_stale_anchor_recreates_tab():
    b = _FakeBackend(
        results=[{"x": 1}],
        tabs_list=[SimpleNamespace(id="OTHER", url="https://gmail.com")],
    )
    ctx = _ctx(anchor_target_id="GONE")  # id no longer in tabs()
    _changed, _notif, new_ctx = await check_watcher(
        b, ctx, passive=True, user_id="u1", job_id="job9",
    )
    assert b.new_tab_calls == [ctx["url"]]
    assert new_ctx["anchor_target_id"] != "GONE"
    assert new_ctx["anchor_target_id"].startswith("NEW")


@pytest.mark.asyncio
async def test_passive_no_url_no_anchor_skips():
    b = _FakeBackend(results=[], tabs_list=[])
    ctx = _ctx(url="")
    changed, notif, new_ctx = await check_watcher(
        b, ctx, passive=True, user_id="u1", job_id="job9",
    )
    assert (changed, notif) == (False, None)
    assert b.new_tab_calls == []
    assert new_ctx is ctx  # unchanged
