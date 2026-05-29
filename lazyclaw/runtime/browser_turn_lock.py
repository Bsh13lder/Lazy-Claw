"""Per-user serialization of live-Brave access across concurrent agent turns.

Root cause (2026-05-29): the foreground chat lane (``user_id``), the heartbeat
lane (``user_id:heartbeat``), and background tasks (bare ``asyncio`` tasks) all
run CONCURRENTLY, and every one of them can drive the user's single live host
Brave on port 9222. Tab selection prefers the existing/MRU tab, so two turns
navigating at the same instant steal each other's tab: a job-research turn and
a proposal-submit turn "cross each other," snapshots come back showing the
wrong page, the loop-guard trips, and nothing completes.

Fix: only ONE browser-driving turn may hold the live Brave per user at a time.
A turn acquires the per-user lock LAZILY on its first browser-family tool call
(``acquire_live_browser_if_needed``) and holds it until the turn ends — the
release is owned by ``browser_turn_scope`` wrapping ``Agent.process_message``.
Other turns block on their first browser call until the holder's turn finishes.

Why turn-granularity (not per-CDP-action): the collision is logical, not
protocol-level. "Turn A navigates → turn B navigates → turn A snapshots B's
page" needs A to own the tab across its whole navigate→read→act sequence, which
spans LLM round-trips. Per-action locking would stop protocol corruption but
not the tab-steal.

Why not a dedicated tab per task: Cloudflare-protected hosts (upwork.com,
linkedin.com) must reuse the ONE signed-in tab — a cold/extra tab fails the JS
challenge silently. See MEMORY ``project_watcher_cloudflare_live_browser``.

Safety: acquisition is bounded by ``ACQUIRE_TIMEOUT_S``. A stuck holder
degrades to today's behaviour (possible race, logged) rather than a permanent
hang. Release always runs in ``browser_turn_scope``'s ``finally``.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# How long a turn waits for the live Brave before giving up and proceeding
# UNLOCKED (degrade, never hang). Generous — a browser turn can legitimately
# hold the Brave for a couple of minutes (multi-step navigate/click/submit).
ACQUIRE_TIMEOUT_S = 180.0

# A heartbeat watcher poll is SKIPPABLE — if a real turn is driving the Brave
# we'd rather skip this poll tick (it retries next interval) than queue behind
# a multi-minute turn. So watcher polls wait only briefly, then skip.
WATCHER_POLL_LOCK_TIMEOUT_S = 8.0

# Tools that drive the user's single live host Brave. whatsapp / instagram /
# email are MCP connectors (Baileys / instagrapi / IMAP) — NOT the shared
# Brave — so they are deliberately excluded and never serialize against it.
_LIVE_BROWSER_TOOLS = frozenset(
    {
        "browser",
        "use_host_browser",
        "watch_site",
        "test_watcher",
        "run_browser_template",
    }
)


def tool_drives_live_browser(tool_name: str | None) -> bool:
    """True when *tool_name* drives the user's one live host Brave session."""
    if not tool_name:
        return False
    name = tool_name.lower()
    if name in _LIVE_BROWSER_TOOLS:
        return True
    # The upwork MCP is browser automation against the same signed-in Brave
    # (tool names look like ``mcp_<uuid>_upwork_*`` or ``upwork_*``).
    return "upwork" in name


# Per-user locks. Module-global (one Brave per user) so that independently
# constructed CDPBackend instances (the daemon builds fresh ones) still
# serialize through the SAME lock, not a per-instance one.
_per_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    lock = _per_user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _per_user_locks[user_id] = lock
    return lock


class _Holder:
    """Per-turn live-Brave lock holder. Exactly one per ``browser_turn_scope``.

    ``_guard`` serializes the check-and-set so concurrent browser tool calls
    WITHIN one turn (e.g. read-only tools running under ``asyncio.gather`` in
    ``ToolExecutor.execute_batch``) cannot both try to take the user lock and
    self-deadlock — the first acquires, the rest see ``held`` and return.
    """

    def __init__(self) -> None:
        self.held = False
        self._lock: asyncio.Lock | None = None
        self._guard = asyncio.Lock()
        # The asyncio task that owns this holder. Used to tell a TRUE same-task
        # nested scope (→ no-op, avoids self-deadlock) from a child task that
        # merely INHERITED this holder via contextvars copy-on-create_task
        # (→ must get its own holder so it serializes against the parent).
        self.task: asyncio.Task | None = None

    async def acquire(self, user_id: str) -> bool:
        """Acquire the per-user live-Brave lock for this turn (idempotent).

        Returns True if the lock is held by this turn, False if acquisition
        timed out (caller proceeds unlocked — degraded, never blocked).
        """
        async with self._guard:
            if self.held:
                return True
            lock = _get_user_lock(user_id)
            try:
                await asyncio.wait_for(lock.acquire(), timeout=ACQUIRE_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning(
                    "Live-browser lock acquire timed out after %.0fs for user %s "
                    "— proceeding UNLOCKED (possible browser race)",
                    ACQUIRE_TIMEOUT_S,
                    user_id[:8],
                )
                return False
            self._lock = lock
            self.held = True
            return True

    async def release(self) -> None:
        async with self._guard:
            if self.held and self._lock is not None:
                try:
                    self._lock.release()
                except RuntimeError:
                    logger.debug(
                        "live-browser lock already released", exc_info=True
                    )
                self.held = False
                self._lock = None


_holder_var: contextvars.ContextVar["_Holder | None"] = contextvars.ContextVar(
    "live_browser_holder", default=None
)


@asynccontextmanager
async def browser_turn_scope():
    """Bracket one agent turn so its live-Brave access is serialized per user.

    Re-entrant for TRUE same-task nesting only (same asyncio task → reuse the
    outer holder, no-op on exit) so it can't self-deadlock. A holder INHERITED
    by a child task (contextvars copies it into ``asyncio.create_task`` — e.g. a
    background TaskRunner job dispatched by a still-running foreground turn) is
    NOT treated as nested: the child gets its OWN holder so it properly
    contends for the per-user lock against its parent. Wrap
    ``Agent.process_message`` with this; the lock is taken lazily inside via
    ``acquire_live_browser_if_needed`` and always released here.
    """
    existing = _holder_var.get()
    current = asyncio.current_task()
    if existing is not None and existing.task is current:
        # Genuine same-task nesting — the outer scope owns holder + release.
        yield
        return
    holder = _Holder()
    holder.task = current
    token = _holder_var.set(holder)
    try:
        yield
    finally:
        await holder.release()
        _holder_var.reset(token)


@asynccontextmanager
async def live_browser_guard(user_id: str, *, timeout: float = ACQUIRE_TIMEOUT_S):
    """Standalone per-user live-Brave lock for callers OUTSIDE an agent turn.

    The heartbeat watcher polls (``daemon._check_watchers`` live path and the
    upwork MCP poll) drive the user's signed-in Brave directly from the tick
    loop — they don't funnel through ``process_message`` so the turn-scope
    holder doesn't cover them. This bracket lets them take the SAME per-user
    lock so a poll can't steal the tab from a foreground/background turn.

    Yields True if the lock was acquired, False if it timed out — callers
    should SKIP their poll when False (retry next interval) rather than drive
    the Brave unguarded.
    """
    lock = _get_user_lock(user_id)
    acquired = False
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        acquired = True
    except asyncio.TimeoutError:
        logger.debug(
            "live_browser_guard busy (%.0fs) for user %s — caller should skip",
            timeout, user_id[:8],
        )
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except RuntimeError:
                logger.debug("live_browser_guard lock already released", exc_info=True)


async def acquire_live_browser_if_needed(user_id: str, tool_name: str) -> bool:
    """Before running *tool_name*: if it drives the live Brave and a turn scope
    is active, take the per-user lock for the rest of the turn (idempotent).

    No-op (returns False) when the tool isn't a browser driver or there's no
    active turn scope (e.g. a direct skill call outside ``process_message``).
    """
    if not tool_drives_live_browser(tool_name):
        return False
    holder = _holder_var.get()
    if holder is None:
        return False
    return await holder.acquire(user_id)
