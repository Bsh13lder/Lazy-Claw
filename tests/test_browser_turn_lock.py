"""Tests for per-user live-Brave serialization across concurrent agent turns.

Root cause (2026-05-29): the foreground chat lane, background tasks, and
watcher turns all run concurrently and all drive the user's single live host
Brave. Two turns navigating at once steal each other's tab — a job-research
turn and a proposal-submit turn "cross each other." Fix: only ONE
browser-driving turn may hold the live Brave per user at a time.
"""

from __future__ import annotations

import asyncio

import pytest

from lazyclaw.runtime import browser_turn_lock as btl
from lazyclaw.runtime.browser_turn_lock import (
    BACKGROUND_ROLE,
    VISIBLE_ROLE,
    acquire_live_browser_if_needed,
    browser_turn_scope,
    current_browser_role,
    tool_drives_live_browser,
)


@pytest.fixture(autouse=True)
def _reset_locks():
    """Each test starts with a clean per-user lock registry."""
    btl._per_user_locks.clear()
    yield
    btl._per_user_locks.clear()


# ── tool detection ───────────────────────────────────────────────────


def test_browser_tools_detected():
    assert tool_drives_live_browser("browser")
    assert tool_drives_live_browser("use_host_browser")
    assert tool_drives_live_browser("watch_site")
    assert tool_drives_live_browser("mcp_489c_upwork_get_conversation")
    assert tool_drives_live_browser("upwork_submit_proposal")


def test_non_browser_tools_excluded():
    # whatsapp / instagram / email are MCP (not the shared Brave) → excluded.
    for name in ("whatsapp_read", "instagram_read_dms", "email_read",
                 "add_task", "save_memory", "search_tools", None):
        assert not tool_drives_live_browser(name), name


# ── serialization: two turns, same user → second waits ───────────────


@pytest.mark.asyncio
async def test_same_user_turns_serialize():
    order: list[str] = []

    async def turn(name: str, hold: float):
        async with browser_turn_scope():
            await acquire_live_browser_if_needed("u1", "browser")
            order.append(f"{name}:start")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    # A starts slightly first and holds the browser; B must wait for A's
    # whole scope to exit before it can start its browser work.
    await asyncio.gather(turn("A", 0.10), _delayed(0.02, turn("B", 0.01)))

    assert order == ["A:start", "A:end", "B:start", "B:end"], order


@pytest.mark.asyncio
async def test_different_users_do_not_block():
    order: list[str] = []

    async def turn(user: str, name: str, hold: float):
        async with browser_turn_scope():
            await acquire_live_browser_if_needed(user, "browser")
            order.append(f"{name}:start")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    # Different users → independent locks → A and B interleave (both start
    # before either ends).
    await asyncio.gather(turn("u1", "A", 0.06), _delayed(0.01, turn("u2", "B", 0.06)))

    assert order[:2] == ["A:start", "B:start"], order


# ── two-lane isolation: visible vs background never block each other ──


@pytest.mark.asyncio
async def test_visible_and_background_lanes_do_not_block():
    """The whole point of the two-lane model: a foreground (visible) turn
    holding the Brave must NOT block a background (watcher/cron) turn — they
    hold different per-(user, role) locks and drive different tabs."""
    order: list[str] = []

    async def turn(role: str, name: str, hold: float):
        async with browser_turn_scope(role):
            await acquire_live_browser_if_needed("u1", "browser")
            order.append(f"{name}:start")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    # Same user, DIFFERENT lanes → independent locks → they interleave (both
    # start before either ends), unlike same-lane turns which serialize.
    await asyncio.gather(
        turn(VISIBLE_ROLE, "fg", 0.06),
        _delayed(0.01, turn(BACKGROUND_ROLE, "bg", 0.06)),
    )

    assert order[:2] == ["fg:start", "bg:start"], order


@pytest.mark.asyncio
async def test_background_role_keys_separate_lock():
    async with browser_turn_scope(BACKGROUND_ROLE):
        await acquire_live_browser_if_needed("u1", "browser")
        # The background lane lock is held; the visible lane is untouched.
        assert btl._get_user_lock("u1", BACKGROUND_ROLE).locked()
        assert not btl._get_user_lock("u1", VISIBLE_ROLE).locked()
    assert not btl._get_user_lock("u1", BACKGROUND_ROLE).locked()


@pytest.mark.asyncio
async def test_background_turns_same_lane_serialize():
    """Within the background lane, turns still serialize (one bg tab)."""
    order: list[str] = []

    async def turn(name: str, hold: float):
        async with browser_turn_scope(BACKGROUND_ROLE):
            await acquire_live_browser_if_needed("u1", "browser")
            order.append(f"{name}:start")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    await asyncio.gather(turn("A", 0.10), _delayed(0.02, turn("B", 0.01)))
    assert order == ["A:start", "A:end", "B:start", "B:end"], order


@pytest.mark.asyncio
async def test_current_browser_role_reflects_scope():
    assert current_browser_role() == VISIBLE_ROLE  # default outside any scope
    async with browser_turn_scope(BACKGROUND_ROLE):
        assert current_browser_role() == BACKGROUND_ROLE
    assert current_browser_role() == VISIBLE_ROLE  # reset on exit


@pytest.mark.asyncio
async def test_guard_background_role_does_not_block_visible_turn():
    """A watcher poll guard on the background lane must NOT be blocked by a
    foreground (visible) turn holding the visible lock — that was the
    starvation bug."""
    from lazyclaw.runtime.browser_turn_lock import live_browser_guard

    # Foreground turn is holding the VISIBLE lane lock.
    visible_lock = btl._get_user_lock("u1", VISIBLE_ROLE)
    await visible_lock.acquire()
    try:
        async with live_browser_guard(
            "u1", role=BACKGROUND_ROLE, timeout=0.5,
        ) as got:
            assert got is True  # background poll acquires despite fg holding
    finally:
        visible_lock.release()


# ── re-entrancy: nested scope is a no-op, outer owns release ──────────


@pytest.mark.asyncio
async def test_nested_scope_is_reentrant():
    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "browser")
        # Nested scope (same task context) must NOT deadlock or release early.
        async with browser_turn_scope():
            await acquire_live_browser_if_needed("u1", "browser")
        # Still inside outer scope — lock still held, so a fresh waiter blocks.
        assert btl._get_user_lock("u1").locked()
    # Outer exited → released.
    assert not btl._get_user_lock("u1").locked()


# ── child task CONTENDS (not treated as nested) — the #4 fix ─────────


@pytest.mark.asyncio
async def test_child_task_contends_not_nested():
    """A background task spawned by a holding turn inherits the parent holder
    via contextvars, but must get its OWN holder and BLOCK on the per-user lock
    until the parent releases — NOT run unlocked as a 'nested' no-op."""
    order: list[str] = []

    async def child():
        async with browser_turn_scope():
            await acquire_live_browser_if_needed("u1", "browser")
            order.append("child:got-lock")

    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "browser")
        order.append("parent:holding")
        # create_task copies the parent context (incl. the parent's holder).
        task = asyncio.create_task(child())
        await asyncio.sleep(0.05)  # child must NOT acquire while parent holds
        order.append("parent:releasing")
    await task

    # If the child were wrongly treated as nested it would append
    # "child:got-lock" BEFORE "parent:releasing".
    assert order == ["parent:holding", "parent:releasing", "child:got-lock"], order


# ── detached-child LEAK regression (delegate / dispatch_subagents) ────
#
# RED before the fix: ``delegate._run_delegate_bg`` and
# ``dispatcher._run_and_publish`` spawn the worker as a DETACHED
# ``asyncio.create_task`` that is never awaited. The parent foreground turn
# (these skills are non-blocking) returns immediately, so the parent's
# ``browser_turn_scope.finally`` runs and is the ONLY releaser. The detached
# child inherits the parent's holder via the contextvars copy but never
# re-enters ``browser_turn_scope``, so when it later acquires the per-user lock
# — AFTER the parent's finally already ran — the lock is taken with NO surviving
# releaser and leaks for the process lifetime (proven: lock held 20:37 → 21:53
# with zero turn activity). The fix wraps each detached child BODY in its own
# ``browser_turn_scope`` so the child gets a FRESH holder (its task identity
# differs from the inherited holder's ``.task``) and releases the lock in its
# own ``finally`` when it finishes.
#
# These tests drive the REAL source coroutines with ``run_specialist``
# replaced by a stub that takes the live-Brave lock (simulating the worker's
# first browser tool call). The simulated detached-task shape: a parent scope
# that has ALREADY exited, then the child coroutine run as its own task.


async def _run_child_as_detached_task(child_coro_factory) -> None:
    """Mimic the delegate/dispatcher shape: hold the lock in a parent scope,
    capture the parent context, exit the parent scope, then run the child in a
    task spawned FROM the captured (parent) context — so the child inherits the
    parent's holder exactly as ``asyncio.create_task`` does in production."""
    import contextvars

    captured: contextvars.Context | None = None
    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "browser")
        captured = contextvars.copy_context()
    # Parent scope released. Run the child in a task using the parent's context
    # (this is what create_task does inside a still-holding parent turn).
    assert captured is not None
    task = captured.run(asyncio.create_task, child_coro_factory())
    await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_dispatcher_run_and_publish_releases_lock(monkeypatch):
    """``AgentDispatcher._run_and_publish`` must NOT leak the live-Brave lock.

    With ``run_specialist`` stubbed to acquire the live-Brave lock (a worker
    browser tool call), running ``_run_and_publish`` as a detached task after
    the parent scope exited must leave the per-user lock UNLOCKED."""
    from lazyclaw.runtime import dispatcher as disp_mod
    from lazyclaw.runtime.dispatcher import (
        AgentDispatcher, AgentType, SubagentConfig,
    )
    from lazyclaw.teams import runner as runner_mod
    from lazyclaw.teams.runner import SpecialistResult

    async def _fake_run_specialist(*args, **kwargs):
        # Simulate the subagent's first browser tool call taking the lock.
        await acquire_live_browser_if_needed("u1", "browser")
        return SpecialistResult(
            agent_name="explore_agent",
            task="lookup X",
            result="ok",
            tools_used=(),
            model_used="worker",
            duration_ms=1,
            success=True,
            error=None,
        )

    monkeypatch.setattr(runner_mod, "run_specialist", _fake_run_specialist)
    # task_event_bus.publish must be a harmless no-op for the test.
    from lazyclaw.runtime import task_event_bus
    monkeypatch.setattr(task_event_bus, "publish", lambda *a, **k: None)

    dispatcher = AgentDispatcher(
        config=None, eco_router=None, registry=None, permission_checker=None,
        team_lead=None, callback=None,
    )
    cfg = SubagentConfig(agent_type=AgentType.EXPLORE, task="lookup X")

    await _run_child_as_detached_task(
        lambda: dispatcher._run_and_publish(cfg, "u1", "subagent-test"),
    )

    assert not btl._get_user_lock("u1").locked()


@pytest.mark.asyncio
async def test_delegate_run_specialist_under_scope_releases_lock():
    """Mirror of ``delegate._run_delegate_bg``'s fixed shape: the detached
    child wraps its ``run_specialist`` call in ``browser_turn_scope``, so the
    lock taken on the worker's first browser tool call is released when the
    worker finishes — no leak after the parent turn already exited."""
    async def fixed_delegate_child() -> None:
        # Same lazy-import + tight-wrap shape as the source fix.
        from lazyclaw.runtime.browser_turn_lock import (
            browser_turn_scope as scope,
        )
        async with scope():
            # Stand-in for run_specialist taking the lock on a browser call.
            await acquire_live_browser_if_needed("u1", "browser")

    await _run_child_as_detached_task(fixed_delegate_child)

    assert not btl._get_user_lock("u1").locked()


# ── standalone guard for heartbeat watcher polls (#6) ────────────────


@pytest.mark.asyncio
async def test_live_browser_guard_acquires_and_releases():
    from lazyclaw.runtime.browser_turn_lock import live_browser_guard

    async with live_browser_guard("u1", timeout=1.0) as got:
        assert got is True
        assert btl._get_user_lock("u1").locked()
    assert not btl._get_user_lock("u1").locked()


@pytest.mark.asyncio
async def test_live_browser_guard_skips_when_busy():
    from lazyclaw.runtime.browser_turn_lock import live_browser_guard

    # A turn is holding the user's Brave lock.
    holder_lock = btl._get_user_lock("u1")
    await holder_lock.acquire()
    try:
        async with live_browser_guard("u1", timeout=0.05) as got:
            # Poll should report busy (False) so the caller skips this tick.
            assert got is False
    finally:
        holder_lock.release()


# ── idempotent acquire within a turn ─────────────────────────────────


@pytest.mark.asyncio
async def test_double_acquire_same_turn_is_idempotent():
    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "browser")
        # Second browser tool in the same turn — must not block on itself.
        await asyncio.wait_for(
            acquire_live_browser_if_needed("u1", "use_host_browser"), timeout=1.0,
        )
        assert btl._get_user_lock("u1").locked()


# ── non-browser tool never acquires ──────────────────────────────────


@pytest.mark.asyncio
async def test_non_browser_tool_does_not_acquire():
    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "whatsapp_read")
        assert not btl._get_user_lock("u1").locked()


# ── no active scope → no-op (direct skill call path) ─────────────────


@pytest.mark.asyncio
async def test_no_scope_is_noop():
    # Calling outside any turn scope must not raise or create a held lock.
    await acquire_live_browser_if_needed("u1", "browser")
    assert not btl._get_user_lock("u1").locked()


# ── timeout degrades to unlocked instead of hanging ──────────────────


@pytest.mark.asyncio
async def test_acquire_timeout_degrades(monkeypatch):
    monkeypatch.setattr(btl, "ACQUIRE_TIMEOUT_S", 0.05)
    # Pre-hold the user lock externally and never release it.
    lock = btl._get_user_lock("u1")
    await lock.acquire()

    async with browser_turn_scope():
        # Should give up after the (patched) timeout and proceed, NOT hang.
        ok = await asyncio.wait_for(
            _acquire_via_holder("u1", "browser"), timeout=2.0,
        )
    assert ok is False


# ── helpers ──────────────────────────────────────────────────────────


async def _delayed(delay: float, coro):
    await asyncio.sleep(delay)
    return await coro


async def _acquire_via_holder(user_id: str, tool_name: str) -> bool:
    """Acquire through the active scope's holder and report success."""
    holder = btl._holder_var.get()
    assert holder is not None
    return await holder.acquire(user_id)
