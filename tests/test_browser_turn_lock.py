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
    acquire_live_browser_if_needed,
    browser_turn_scope,
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


# ── re-entrancy: nested scope is a no-op, outer owns release ──────────


@pytest.mark.asyncio
async def test_nested_scope_is_reentrant():
    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "browser")
        # Nested scope (same task context) must NOT deadlock or release early.
        async with browser_turn_scope():
            await acquire_live_browser_if_needed("u1", "browser")
        # Still inside outer scope — lock still held, so a fresh waiter blocks.
        assert btl._per_user_locks["u1"].locked()
    # Outer exited → released.
    assert not btl._per_user_locks["u1"].locked()


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


# ── standalone guard for heartbeat watcher polls (#6) ────────────────


@pytest.mark.asyncio
async def test_live_browser_guard_acquires_and_releases():
    from lazyclaw.runtime.browser_turn_lock import live_browser_guard

    async with live_browser_guard("u1", timeout=1.0) as got:
        assert got is True
        assert btl._per_user_locks["u1"].locked()
    assert not btl._per_user_locks["u1"].locked()


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
        assert btl._per_user_locks["u1"].locked()


# ── non-browser tool never acquires ──────────────────────────────────


@pytest.mark.asyncio
async def test_non_browser_tool_does_not_acquire():
    async with browser_turn_scope():
        await acquire_live_browser_if_needed("u1", "whatsapp_read")
        assert "u1" not in btl._per_user_locks or not btl._per_user_locks["u1"].locked()


# ── no active scope → no-op (direct skill call path) ─────────────────


@pytest.mark.asyncio
async def test_no_scope_is_noop():
    # Calling outside any turn scope must not raise or create a held lock.
    await acquire_live_browser_if_needed("u1", "browser")
    assert "u1" not in btl._per_user_locks or not btl._per_user_locks["u1"].locked()


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
