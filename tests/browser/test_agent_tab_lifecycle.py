"""SAFETY-CRITICAL unit tests for the foreground-agent tab lifecycle.

Context (2026-06-09): there is ONE signed-in Brave per user. Watcher / cron
lanes park on their OWN tabs (``owned_tabs`` keys ``watch:{job_id}`` /
``background``), preserved by the tab reaper. The FOREGROUND agent's tab was
previously UNREGISTERED — it rode the MRU front tab, so stray tabs shuffled it
onto the wrong tab, and it never closed its own tabs (RAM grows).

This feature:
  1. Pins + names the foreground agent's tab under ``owned_tabs`` key
     ``"agent"`` (``AGENT_KEY``) so it always reuses ITS tab.
  2. Auto-closes on turn-end — but ONLY tabs the agent itself CREATED, never a
     tab it BORROWED (reused a pre-existing user tab, e.g. the signed-in Upwork
     tab for Cloudflare). **This is the inviolable safety invariant.**

These tests are UNIT-LEVEL ONLY: they exercise the real ``owned_tabs`` registry
plus a tiny fake backend. NO real browser, NO CDP connection is ever opened.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from lazyclaw.browser import owned_tabs


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts from an empty registry + empty agent-created set."""
    from lazyclaw.runtime import browser_turn_lock as _btl

    owned_tabs._registry.clear()
    owned_tabs._agent_created.clear()
    _btl._per_user_locks.clear()
    yield
    owned_tabs._registry.clear()
    owned_tabs._agent_created.clear()
    _btl._per_user_locks.clear()


# ── tiny fake backend (NO real browser) ──────────────────────────────


@dataclass
class _FakeTab:
    id: str
    url: str = "about:blank"
    title: str = ""
    active: bool = False


class _FakeBackend:
    """Minimal CDP-backend stand-in: ``new_tab`` / ``close_tab`` / ``tabs``.

    Returns target-id dicts; records every close so tests can assert exactly
    which tabs were touched. Opening a real browser is NEVER attempted.
    """

    def __init__(self, tabs: list[_FakeTab] | None = None) -> None:
        self._tabs: list[_FakeTab] = list(tabs or [])
        self.closed: list[str] = []
        self._counter = 0

    async def tabs(self) -> list[_FakeTab]:
        return list(self._tabs)

    async def new_tab(self, url: str = "about:blank", *, background: bool = False) -> str:
        self._counter += 1
        tid = f"NEW_{self._counter}"
        self._tabs.append(_FakeTab(id=tid, url=url))
        return tid

    async def close_tab(self, target_id: str) -> None:
        self.closed.append(target_id)
        self._tabs = [t for t in self._tabs if t.id != target_id]


# ── 1. register an agent tab under key "agent" + read it back ─────────


def test_agent_key_is_literal_agent():
    assert owned_tabs.AGENT_KEY == "agent"


def test_register_and_read_back_agent_tab():
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_TAB")
    assert owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY) == "AGENT_TAB"
    assert "AGENT_TAB" in owned_tabs.all_owned_target_ids("user-a")


# ── 2. created-vs-borrowed distinction ───────────────────────────────


def test_created_tab_is_flagged_closeable():
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "CREATED_TAB")
    owned_tabs.mark_agent_created("user-a", "CREATED_TAB")
    assert owned_tabs.is_agent_created("user-a", "CREATED_TAB") is True


def test_borrowed_tab_is_not_flagged_created():
    # The agent reused a pre-existing user tab — NEVER mark it created.
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "BORROWED_TAB")
    assert owned_tabs.is_agent_created("user-a", "BORROWED_TAB") is False


def test_agent_created_is_user_isolated():
    owned_tabs.mark_agent_created("user-a", "T1")
    assert owned_tabs.is_agent_created("user-a", "T1") is True
    assert owned_tabs.is_agent_created("user-b", "T1") is False


def test_clear_agent_created_for_one_id():
    owned_tabs.mark_agent_created("user-a", "T1")
    owned_tabs.mark_agent_created("user-a", "T2")
    owned_tabs.clear_agent_created("user-a", "T1")
    assert owned_tabs.is_agent_created("user-a", "T1") is False
    assert owned_tabs.is_agent_created("user-a", "T2") is True


def test_clear_agent_created_all_for_user():
    owned_tabs.mark_agent_created("user-a", "T1")
    owned_tabs.mark_agent_created("user-a", "T2")
    owned_tabs.clear_agent_created("user-a")
    assert owned_tabs.is_agent_created("user-a", "T1") is False
    assert owned_tabs.is_agent_created("user-a", "T2") is False


def test_clear_agent_created_missing_is_noop():
    owned_tabs.clear_agent_created("ghost")
    owned_tabs.clear_agent_created("ghost", "nope")


# ── 3. the pure decision function should_close_agent_tab ──────────────
#
# CLOSE only when ALL hold:
#   * created_by_agent       (NEVER close a borrowed tab)
#   * total_tabs > 1         (NEVER close the only tab)
#   * target_id not a watcher/background-owned id
#   * target_id is truthy (still the agent-owned id)


def test_close_decision_close_for_created_multi_tab():
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=True,
        target_id="AGENT_TAB",
        total_tabs=3,
        watcher_target_ids=frozenset({"W1"}),
    ) is True


def test_close_decision_keep_for_borrowed_tab():
    # THE inviolable invariant — a borrowed tab is NEVER closed.
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=False,
        target_id="BORROWED_TAB",
        total_tabs=5,
        watcher_target_ids=frozenset(),
    ) is False


def test_close_decision_keep_when_only_tab():
    # Closing the last tab would leave the user with no window — never do it.
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=True,
        target_id="AGENT_TAB",
        total_tabs=1,
        watcher_target_ids=frozenset(),
    ) is False


def test_close_decision_keep_when_zero_tabs():
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=True,
        target_id="AGENT_TAB",
        total_tabs=0,
        watcher_target_ids=frozenset(),
    ) is False


def test_close_decision_keep_when_id_is_watcher_owned():
    # Defense-in-depth: even a "created" id that is ALSO a watcher/background
    # owned id must never be closed by agent logic.
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=True,
        target_id="WATCHER_TAB",
        total_tabs=4,
        watcher_target_ids=frozenset({"WATCHER_TAB", "BG_TAB"}),
    ) is False


def test_close_decision_keep_when_no_target_id():
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=True,
        target_id=None,
        total_tabs=3,
        watcher_target_ids=frozenset(),
    ) is False
    assert owned_tabs.should_close_agent_tab(
        created_by_agent=True,
        target_id="",
        total_tabs=3,
        watcher_target_ids=frozenset(),
    ) is False


# ── 4. reaper anchoring: agent tabs reapable, watcher/bg anchored ─────


def test_anchored_excluding_agent_drops_agent_keeps_watchers():
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_TAB")
    owned_tabs.set_owned("user-a", "background", "BG_TAB")
    owned_tabs.set_owned("user-a", "watch:job1", "W1")
    owned_tabs.set_owned("user-a", "watch:job2", "W2")

    anchored = owned_tabs.anchored_target_ids_excluding_agent("user-a")
    # watcher + background stay anchored
    assert "BG_TAB" in anchored
    assert "W1" in anchored
    assert "W2" in anchored
    # the agent tab is NOT anchored — it must remain idle-reapable
    assert "AGENT_TAB" not in anchored


def test_all_owned_still_includes_agent_for_mru_exclusion():
    # The VISIBLE MRU pick still excludes the agent tab via all_owned_target_ids
    # so a second visible turn doesn't grab a watcher tab — only the REAPER
    # anchor set drops the agent key.
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_TAB")
    owned_tabs.set_owned("user-a", "watch:job1", "W1")
    assert owned_tabs.all_owned_target_ids("user-a") == frozenset(
        {"AGENT_TAB", "W1"}
    )


def test_anchored_excluding_agent_empty_user():
    assert owned_tabs.anchored_target_ids_excluding_agent("nobody") == frozenset()


def test_anchored_excluding_agent_only_agent_tab():
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_TAB")
    assert owned_tabs.anchored_target_ids_excluding_agent("user-a") == frozenset()


# ── 5. end-to-end close decision wired through the fake backend ───────


@pytest.mark.asyncio
async def test_created_agent_tab_closed_on_turn_end():
    """Created agent tab + a borrowed sibling → only the created one closes."""
    backend = _FakeBackend(
        tabs=[_FakeTab(id="BORROWED", url="https://upwork.com"),
              _FakeTab(id="AGENT_NEW", url="https://example.com")]
    )
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_NEW")
    owned_tabs.mark_agent_created("user-a", "AGENT_NEW")

    total = len(await backend.tabs())
    watcher_ids = owned_tabs.anchored_target_ids_excluding_agent("user-a")
    agent_id = owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY)
    if owned_tabs.should_close_agent_tab(
        created_by_agent=owned_tabs.is_agent_created("user-a", agent_id),
        target_id=agent_id,
        total_tabs=total,
        watcher_target_ids=watcher_ids,
    ):
        await backend.close_tab(agent_id)
        owned_tabs.clear_owned("user-a", owned_tabs.AGENT_KEY)
        owned_tabs.clear_agent_created("user-a", agent_id)

    assert backend.closed == ["AGENT_NEW"]
    assert "BORROWED" not in backend.closed
    assert owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY) is None
    assert owned_tabs.is_agent_created("user-a", "AGENT_NEW") is False


@pytest.mark.asyncio
async def test_borrowed_agent_tab_never_closed_on_turn_end():
    """Agent BORROWED the signed-in Upwork tab → it is NEVER closed."""
    backend = _FakeBackend(
        tabs=[_FakeTab(id="UPWORK", url="https://www.upwork.com/messages"),
              _FakeTab(id="OTHER", url="https://example.com")]
    )
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "UPWORK")
    # NOT marked created — it was borrowed.

    total = len(await backend.tabs())
    watcher_ids = owned_tabs.anchored_target_ids_excluding_agent("user-a")
    agent_id = owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY)
    if owned_tabs.should_close_agent_tab(
        created_by_agent=owned_tabs.is_agent_created("user-a", agent_id),
        target_id=agent_id,
        total_tabs=total,
        watcher_target_ids=watcher_ids,
    ):
        await backend.close_tab(agent_id)

    assert backend.closed == []  # nothing closed — borrowed tab survives


@pytest.mark.asyncio
async def test_watcher_tab_never_closed_even_if_marked_created():
    """If the agent-owned id collides with a watcher id, agent never closes it."""
    backend = _FakeBackend(
        tabs=[_FakeTab(id="W1", url="https://www.upwork.com"),
              _FakeTab(id="X", url="https://example.com")]
    )
    owned_tabs.set_owned("user-a", "watch:job1", "W1")
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "W1")
    owned_tabs.mark_agent_created("user-a", "W1")

    total = len(await backend.tabs())
    watcher_ids = owned_tabs.anchored_target_ids_excluding_agent("user-a")
    agent_id = owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY)
    if owned_tabs.should_close_agent_tab(
        created_by_agent=owned_tabs.is_agent_created("user-a", agent_id),
        target_id=agent_id,
        total_tabs=total,
        watcher_target_ids=watcher_ids,
    ):
        await backend.close_tab(agent_id)

    assert backend.closed == []


# ── 6. the real turn-end finally helper (_close_agent_tab_if_created) ──
#
# Exercises the ACTUAL production code path in browser_turn_lock, with
# get_cdp_backend monkeypatched to a fake backend (NO real browser).


@pytest.fixture
def _patch_backend(monkeypatch):
    """Patch get_cdp_backend so the finally helper uses a fake backend."""
    holder: dict[str, _FakeBackend] = {}

    async def _fake_get_cdp_backend(user_id: str = "default"):
        return holder["backend"]

    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.backends.get_cdp_backend",
        _fake_get_cdp_backend,
    )
    return holder


@pytest.mark.asyncio
async def test_finally_closes_created_tab(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import _close_agent_tab_if_created

    backend = _FakeBackend(
        tabs=[_FakeTab(id="BORROWED", url="https://x.com"),
              _FakeTab(id="AGENT_NEW", url="https://example.com")]
    )
    _patch_backend["backend"] = backend
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_NEW")
    owned_tabs.mark_agent_created("user-a", "AGENT_NEW")

    await _close_agent_tab_if_created("user-a")

    assert backend.closed == ["AGENT_NEW"]
    assert owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY) is None
    assert owned_tabs.is_agent_created("user-a", "AGENT_NEW") is False


@pytest.mark.asyncio
async def test_finally_never_closes_borrowed_tab(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import _close_agent_tab_if_created

    backend = _FakeBackend(
        tabs=[_FakeTab(id="UPWORK", url="https://www.upwork.com"),
              _FakeTab(id="OTHER", url="https://x.com")]
    )
    _patch_backend["backend"] = backend
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "UPWORK")
    # borrowed → not marked created

    await _close_agent_tab_if_created("user-a")

    assert backend.closed == []
    # the pin is forgotten but the tab is untouched
    assert owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY) is None


@pytest.mark.asyncio
async def test_finally_keeps_created_tab_when_only_tab(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import _close_agent_tab_if_created

    backend = _FakeBackend(tabs=[_FakeTab(id="AGENT_NEW", url="https://x.com")])
    _patch_backend["backend"] = backend
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_NEW")
    owned_tabs.mark_agent_created("user-a", "AGENT_NEW")

    await _close_agent_tab_if_created("user-a")

    assert backend.closed == []  # only tab — never closed
    # still pinned (we didn't close it, so we keep the pin)
    assert owned_tabs.get_owned("user-a", owned_tabs.AGENT_KEY) == "AGENT_NEW"


@pytest.mark.asyncio
async def test_finally_no_agent_pin_is_noop(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import _close_agent_tab_if_created

    backend = _FakeBackend(tabs=[_FakeTab(id="X", url="https://x.com")])
    _patch_backend["backend"] = backend
    # no agent key registered at all
    await _close_agent_tab_if_created("user-a")
    assert backend.closed == []


@pytest.mark.asyncio
async def test_finally_close_failure_never_raises(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import _close_agent_tab_if_created

    class _BoomBackend(_FakeBackend):
        async def close_tab(self, target_id: str) -> None:
            raise RuntimeError("CDP died mid-close")

    backend = _BoomBackend(
        tabs=[_FakeTab(id="A", url="https://x.com"),
              _FakeTab(id="AGENT_NEW", url="https://y.com")]
    )
    _patch_backend["backend"] = backend
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_NEW")
    owned_tabs.mark_agent_created("user-a", "AGENT_NEW")

    # Must swallow the exception — a close failure can never break the turn.
    await _close_agent_tab_if_created("user-a")


# ── 7. browser_turn_scope: background lane never auto-closes ──────────


@pytest.mark.asyncio
async def test_background_lane_scope_never_closes(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import (
        BACKGROUND_ROLE,
        browser_turn_scope,
    )

    backend = _FakeBackend(
        tabs=[_FakeTab(id="BG", url="https://x.com"),
              _FakeTab(id="OTHER", url="https://y.com")]
    )
    _patch_backend["backend"] = backend
    # Even if (wrongly) an agent key + created flag were present, the
    # background lane must NEVER run the auto-close.
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "BG")
    owned_tabs.mark_agent_created("user-a", "BG")

    async with browser_turn_scope(BACKGROUND_ROLE, user_id="user-a"):
        pass

    assert backend.closed == []


@pytest.mark.asyncio
async def test_visible_lane_scope_closes_created(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import (
        VISIBLE_ROLE,
        browser_turn_scope,
    )

    backend = _FakeBackend(
        tabs=[_FakeTab(id="A", url="https://x.com"),
              _FakeTab(id="AGENT_NEW", url="https://y.com")]
    )
    _patch_backend["backend"] = backend
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_NEW")
    owned_tabs.mark_agent_created("user-a", "AGENT_NEW")

    async with browser_turn_scope(VISIBLE_ROLE, user_id="user-a"):
        pass

    assert backend.closed == ["AGENT_NEW"]


@pytest.mark.asyncio
async def test_visible_lane_scope_no_user_id_is_noop(_patch_backend):
    from lazyclaw.runtime.browser_turn_lock import (
        VISIBLE_ROLE,
        browser_turn_scope,
    )

    backend = _FakeBackend(
        tabs=[_FakeTab(id="A", url="https://x.com"),
              _FakeTab(id="AGENT_NEW", url="https://y.com")]
    )
    _patch_backend["backend"] = backend
    owned_tabs.set_owned("user-a", owned_tabs.AGENT_KEY, "AGENT_NEW")
    owned_tabs.mark_agent_created("user-a", "AGENT_NEW")

    # No user_id → auto-close path is skipped entirely (back-compat callers).
    async with browser_turn_scope(VISIBLE_ROLE):
        pass

    assert backend.closed == []
