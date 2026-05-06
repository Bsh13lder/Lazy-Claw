"""Tests for tab housekeeping — sweep_idle_tabs and action_close_tab.

These cover the pure logic of the tab-pruning helpers without spinning
up a real browser. The backend is faked with a small stub that mimics
``CDPBackend.tabs`` and ``CDPBackend.close_tab``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from lazyclaw.browser.cdp_backend import TabInfo
from lazyclaw.skills.builtin.browser_actions.navigation import (
    DEFAULT_MAX_TABS,
    _is_pinned_url,
    action_close_tab,
    sweep_idle_tabs,
)


@dataclass
class _FakeBackend:
    """Stand-in for CDPBackend used by sweep_idle_tabs / action_close_tab."""

    tab_list: list[TabInfo]
    closed: list[str] = field(default_factory=list)

    async def tabs(self) -> list[TabInfo]:
        return list(self.tab_list)

    async def close_tab(self, target_id: str) -> None:
        self.closed.append(target_id)
        self.tab_list = [t for t in self.tab_list if t.id != target_id]


def _make_tab(idx: int, *, active: bool = False, url: str | None = None) -> TabInfo:
    return TabInfo(
        id=f"id-{idx}",
        title=f"Tab {idx}",
        url=url if url is not None else f"https://example.com/{idx}",
        active=active,
    )


# --- _is_pinned_url ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "",
    "about:blank",
    "chrome://newtab",
    "chrome-extension://abcdef/popup.html",
    "brave://settings",
    "devtools://devtools/inspector.html",
    "edge://flags",
])
def test_pinned_urls_are_protected(url: str) -> None:
    assert _is_pinned_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.upwork.com/nx/find-work",
    "https://github.com/anthropics",
    "http://localhost:8000/dev",
])
def test_regular_urls_are_not_pinned(url: str) -> None:
    assert _is_pinned_url(url) is False


# --- sweep_idle_tabs -----------------------------------------------------

@pytest.mark.asyncio
async def test_sweep_no_op_when_under_cap() -> None:
    """Cap not exceeded → no closes."""
    tabs = [_make_tab(0, active=True), _make_tab(1), _make_tab(2)]
    backend = _FakeBackend(tab_list=tabs)
    closed = await sweep_idle_tabs(backend, max_tabs=8)
    assert closed == 0
    assert backend.closed == []


@pytest.mark.asyncio
async def test_sweep_closes_oldest_tail_first() -> None:
    """Brave returns MRU order — sweep walks from the end."""
    # 5 tabs, cap=3, expect 2 closed: id-4 (oldest), id-3 (next).
    tabs = [
        _make_tab(0, active=True),
        _make_tab(1),
        _make_tab(2),
        _make_tab(3),
        _make_tab(4),
    ]
    backend = _FakeBackend(tab_list=tabs)
    closed = await sweep_idle_tabs(backend, max_tabs=3)
    assert closed == 2
    assert backend.closed == ["id-4", "id-3"]


@pytest.mark.asyncio
async def test_sweep_never_closes_active_tab() -> None:
    """Even if the active tab is the oldest in the list, it stays."""
    # Active tab is at the *tail* (artificial — Chromium always puts it
    # at the head, but defensive coverage in case discovery returns
    # tabs in a different order).
    tabs = [
        _make_tab(0),
        _make_tab(1),
        _make_tab(2, active=True),
    ]
    backend = _FakeBackend(tab_list=tabs)
    closed = await sweep_idle_tabs(backend, max_tabs=1)
    # Cap=1, 3 tabs → overflow=2, but only id-1 and id-0 are eligible
    # (id-2 is active). Both should close, none of which is the active.
    assert closed == 2
    assert "id-2" not in backend.closed
    assert set(backend.closed) == {"id-0", "id-1"}


@pytest.mark.asyncio
async def test_sweep_skips_pinned_system_tabs() -> None:
    """chrome:// / brave:// / about:blank are preserved."""
    tabs = [
        _make_tab(0, active=True),
        _make_tab(1, url="chrome://settings"),
        _make_tab(2, url="brave://newtab"),
        _make_tab(3, url="about:blank"),
        _make_tab(4),
        _make_tab(5),
    ]
    backend = _FakeBackend(tab_list=tabs)
    closed = await sweep_idle_tabs(backend, max_tabs=2)
    # Overflow=4, but only id-5 and id-4 are sweepable. The system tabs
    # stay because they are pinned, even though they're "older" by MRU.
    assert closed == 2
    assert set(backend.closed) == {"id-4", "id-5"}


@pytest.mark.asyncio
async def test_sweep_handles_backend_error_gracefully() -> None:
    """If tabs() throws, sweep returns 0 instead of bubbling."""

    class _BrokenBackend:
        async def tabs(self) -> list[TabInfo]:
            raise RuntimeError("CDP gone")

        async def close_tab(self, target_id: str) -> None:  # pragma: no cover
            raise AssertionError("should not be called")

    closed = await sweep_idle_tabs(_BrokenBackend(), max_tabs=5)
    assert closed == 0


@pytest.mark.asyncio
async def test_sweep_continues_after_individual_close_failure() -> None:
    """One failing close doesn't stop the rest of the sweep."""

    class _PartialFailBackend(_FakeBackend):
        async def close_tab(self, target_id: str) -> None:
            if target_id == "id-3":
                raise RuntimeError("flaky CDP")
            await super().close_tab(target_id)

    tabs = [
        _make_tab(0, active=True),
        _make_tab(1),
        _make_tab(2),
        _make_tab(3),
        _make_tab(4),
    ]
    backend = _PartialFailBackend(tab_list=tabs)
    closed = await sweep_idle_tabs(backend, max_tabs=3)
    # Overflow=2. Tail walk: id-4 closes, id-3 raises (skipped, not retried).
    # Net result: only 1 successful close.
    assert closed == 1
    assert backend.closed == ["id-4"]


# --- action_close_tab ----------------------------------------------------

@pytest.mark.asyncio
async def test_close_tab_requires_target(monkeypatch) -> None:
    """Empty target → POLICY_DENIED error string."""

    async def fake_get_cdp_backend(_uid):  # pragma: no cover
        raise AssertionError("backend must not be touched")

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.navigation.get_cdp_backend",
        fake_get_cdp_backend,
    )
    out = await action_close_tab("u1", {"target": ""})
    assert "[policy_denied]" in out


@pytest.mark.asyncio
async def test_close_tab_by_index(monkeypatch) -> None:
    backend = _FakeBackend(tab_list=[
        _make_tab(0, active=True),
        _make_tab(1),
        _make_tab(2),
    ])

    async def fake_get_cdp_backend(_uid):
        return backend

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.navigation.get_cdp_backend",
        fake_get_cdp_backend,
    )

    out = await action_close_tab("u1", {"target": "2"})
    assert backend.closed == ["id-1"]
    assert "Closed tab" in out


@pytest.mark.asyncio
async def test_close_tab_by_url_substring(monkeypatch) -> None:
    backend = _FakeBackend(tab_list=[
        _make_tab(0, active=True, url="https://google.com/search"),
        _make_tab(1, url="https://www.upwork.com/jobs/123"),
        _make_tab(2, url="https://github.com"),
    ])

    async def fake_get_cdp_backend(_uid):
        return backend

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.navigation.get_cdp_backend",
        fake_get_cdp_backend,
    )

    out = await action_close_tab("u1", {"target": "upwork"})
    assert backend.closed == ["id-1"]
    assert "Closed tab" in out


@pytest.mark.asyncio
async def test_close_tab_refuses_active(monkeypatch) -> None:
    backend = _FakeBackend(tab_list=[
        _make_tab(0, active=True, url="https://github.com"),
        _make_tab(1, url="https://example.com"),
    ])

    async def fake_get_cdp_backend(_uid):
        return backend

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.navigation.get_cdp_backend",
        fake_get_cdp_backend,
    )

    out = await action_close_tab("u1", {"target": "github"})
    assert backend.closed == []
    assert "active tab" in out.lower()


@pytest.mark.asyncio
async def test_close_tab_refuses_last_tab(monkeypatch) -> None:
    backend = _FakeBackend(tab_list=[_make_tab(0, active=True)])

    async def fake_get_cdp_backend(_uid):
        return backend

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.navigation.get_cdp_backend",
        fake_get_cdp_backend,
    )

    out = await action_close_tab("u1", {"target": "1"})
    assert backend.closed == []
    assert "only open tab" in out.lower()


@pytest.mark.asyncio
async def test_close_tab_no_match(monkeypatch) -> None:
    backend = _FakeBackend(tab_list=[
        _make_tab(0, active=True),
        _make_tab(1, url="https://example.com"),
    ])

    async def fake_get_cdp_backend(_uid):
        return backend

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.navigation.get_cdp_backend",
        fake_get_cdp_backend,
    )

    out = await action_close_tab("u1", {"target": "nonexistent-domain"})
    assert backend.closed == []
    assert "No tab matches" in out


# --- DEFAULT_MAX_TABS sanity --------------------------------------------

def test_default_max_tabs_is_reasonable() -> None:
    """Cap must be high enough to not break normal multi-tab work."""
    assert 4 <= DEFAULT_MAX_TABS <= 20
