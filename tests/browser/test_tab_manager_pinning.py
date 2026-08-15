"""Tab-pinning tests — TabManager specialist tabs must be owned + anchored.

Context (2026-08-15, porting vercel-labs/agent-browser session-pinning):
parallel browser specialists each get a tab from ``TabManager`` on the ONE
signed-in Brave. Those tabs were UNREGISTERED in ``owned_tabs``, so:
  * the foreground agent's ``_pick_preferred_tab`` (which only excludes
    registered tabs) could land on and HIJACK a specialist's tab, and
  * the tab reaper (anchors registered non-agent tabs) could close one
    mid-task.
The fix registers each specialist tab under a ``specialist:<domain>`` lane,
creates it in the background (no focus-steal), and re-validates the pinned
target on idle-reuse (recreate if the tab was closed underneath us).

UNIT-LEVEL: real ``owned_tabs`` registry + a fake backend. No real browser.
"""

from __future__ import annotations

import pytest

from lazyclaw.browser import owned_tabs
from lazyclaw.browser.tab_manager import TabManager


@pytest.fixture(autouse=True)
def _clean_registry():
    owned_tabs._registry.clear()
    owned_tabs._agent_created.clear()
    yield
    owned_tabs._registry.clear()
    owned_tabs._agent_created.clear()


class _FakeTab:
    def __init__(self, tab_id: str) -> None:
        self.id = tab_id


class FakeBackend:
    """Minimal CDPBackend stand-in for TabManager (no real CDP)."""

    def __init__(self, user_id: str = "u1") -> None:
        self._user_id = user_id
        self._counter = 0
        self.created: list[tuple[str, bool]] = []   # (target_id, background)
        self.closed: list[str] = []
        self._alive: set[str] = set()

    async def new_tab(self, url: str = "about:blank", *, background: bool = False) -> str:
        self._counter += 1
        target_id = f"tab-{self._counter}"
        self.created.append((target_id, background))
        self._alive.add(target_id)
        return target_id

    async def attach_to_target(self, target_id: str) -> str:
        return f"sess-{target_id}"

    async def close_tab(self, target_id: str) -> None:
        self.closed.append(target_id)
        self._alive.discard(target_id)

    async def tabs(self):
        return [_FakeTab(t) for t in self._alive]

    def kill_tab_underneath(self, target_id: str) -> None:
        """Simulate the tab being closed by the user / reaper / a crash."""
        self._alive.discard(target_id)


async def test_specialist_tab_registered_and_anchored_on_acquire():
    be = FakeBackend("u1")
    mgr = TabManager(be)
    ctx = await mgr.acquire("https://himap.co/admin", "browser")

    owned = owned_tabs.all_owned_target_ids("u1")
    assert ctx.target_id in owned, "specialist tab must be registered as owned"
    # Reaper anchors everything except the agent key → specialist tab safe.
    anchored = owned_tabs.anchored_target_ids_excluding_agent("u1")
    assert ctx.target_id in anchored


async def test_specialist_tab_created_in_background():
    be = FakeBackend("u1")
    mgr = TabManager(be)
    await mgr.acquire("https://himap.co/admin", "browser")
    assert be.created, "a tab should have been created"
    assert be.created[0][1] is True, "specialist tabs must open in background"


async def test_release_close_clears_ownership():
    be = FakeBackend("u1")
    mgr = TabManager(be)
    ctx = await mgr.acquire("https://himap.co/admin", "browser")
    await mgr.release(ctx.domain, close=True)
    assert ctx.target_id not in owned_tabs.all_owned_target_ids("u1")


async def test_idle_release_keeps_ownership():
    # Idle (not closed) tab is reused later — it must stay owned so the
    # reaper doesn't reap it and the foreground pick doesn't hijack it.
    be = FakeBackend("u1")
    mgr = TabManager(be)
    ctx = await mgr.acquire("https://himap.co/admin", "browser")
    await mgr.release(ctx.domain, close=False)
    assert ctx.target_id in owned_tabs.all_owned_target_ids("u1")


async def test_cleanup_clears_all_ownership():
    be = FakeBackend("u1")
    mgr = TabManager(be)
    await mgr.acquire("https://a.com", "browser")
    await mgr.acquire("https://b.com", "browser")
    await mgr.cleanup()
    assert owned_tabs.all_owned_target_ids("u1") == frozenset()


async def test_eviction_clears_old_registers_new():
    be = FakeBackend("u1")
    mgr = TabManager(be, max_tabs=1)
    a = await mgr.acquire("https://a.com", "browser")
    await mgr.release(a.domain, close=False)          # idle, evictable
    b = await mgr.acquire("https://b.com", "browser")  # forces eviction of a
    owned = owned_tabs.all_owned_target_ids("u1")
    assert a.target_id not in owned, "evicted tab must be un-owned"
    assert b.target_id in owned
    assert a.target_id in be.closed


async def test_stale_pin_recreated_on_reuse():
    be = FakeBackend("u1")
    mgr = TabManager(be)
    a = await mgr.acquire("https://himap.co", "browser")
    await mgr.release(a.domain, close=False)          # idle, reusable
    be.kill_tab_underneath(a.target_id)                # closed underneath us
    b = await mgr.acquire("https://himap.co", "browser")
    assert b.target_id != a.target_id, "dead pin must be replaced, not reused"
    owned = owned_tabs.all_owned_target_ids("u1")
    assert b.target_id in owned
    assert a.target_id not in owned


async def test_foreground_pick_excludes_specialist_tab():
    """The crux: a registered specialist tab is never picked by the
    foreground agent lane, so it cannot be hijacked."""
    from lazyclaw.browser.cdp import CDPTab
    from lazyclaw.browser.cdp_backend import CDPBackend

    be = FakeBackend("u7")
    mgr = TabManager(be)
    spec = await mgr.acquire("https://himap.co/admin", "browser")

    fg = CDPBackend(port=9999, user_id="u7")
    page_tabs = [
        CDPTab(id=spec.target_id, title="spec", url="https://himap.co/admin",
               ws_url="", tab_type="page"),
        CDPTab(id="user-tab", title="user", url="https://example.com",
               ws_url="", tab_type="page"),
    ]
    picked = fg._pick_preferred_tab(page_tabs)
    assert picked.id == "user-tab", "foreground must not grab a specialist tab"
