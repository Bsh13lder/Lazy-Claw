"""Process-global registry of browser tabs OWNED by a specific execution lane.

Why this exists (2026-06-02): there is one signed-in Brave per user, and the
foreground agent, background watcher/cron brain turns, and watcher polls all
drive it over CDP. They used to fight over the single MRU/visible tab. The fix
is a two-lane browser model — the foreground uses the visible tab while
background work uses its OWN tab(s). This registry is the shared bookkeeping
that makes the two lanes coexist:

  * the VISIBLE backend reads :func:`all_owned_target_ids` and EXCLUDES those
    tabs from its MRU pick, so it never lands on a background tab;
  * the tab reaper reads the same set and ANCHORS those tabs, so it never
    closes or white-screen-reloads a tab a background lane is parked on.

It is module-global (keyed by ``user_id``) on purpose: the heartbeat daemon
builds a fresh ``CDPBackend`` every tick, so per-backend state would be lost.
The ``target_id`` values are Chromium ``/json`` ids (== CDP ``targetId``),
which are stable for the life of a tab and re-resolvable by
``CDPBackend.switch_tab``.

Keys (``target_key``) name the owning lane/role:
  * ``"background"`` — the shared tab for background brain turns (cron,
    reminder, watcher-on-change).
  * ``f"watch:{job_id}"`` — the parked tab for one watcher's zero-LLM poll.
  * ``"agent"`` (:data:`AGENT_KEY`) — the FOREGROUND chat agent's working tab
    (2026-06-09). Pinning it stops the agent shuffling onto a stray tab AND
    lets the agent reuse exactly its own tab turn-to-turn. UNLIKE watcher /
    background tabs, the agent tab is CLOSEABLE: it is anchored for MRU
    EXCLUSION (so a second visible turn doesn't grab a watcher tab) but is
    EXCLUDED from the reaper's anchor set (so a missed scope-close still gets
    idle-reaped). See :func:`anchored_target_ids_excluding_agent`.

Created-vs-borrowed (SAFETY-CRITICAL, 2026-06-09): the agent may either CREATE
a fresh tab (its own ``new_tab``) or BORROW a pre-existing user tab (e.g. the
signed-in Upwork tab, which MUST be reused so Cloudflare's fingerprint passes).
``_agent_created`` records which agent ``target_id`` values were CREATED. The
turn-end auto-close (:func:`should_close_agent_tab`) closes ONLY created tabs —
a borrowed user tab is NEVER closed. This is an inviolable invariant.

Immutability note: reads return a fresh ``frozenset`` / plain value; the
internal dict is only mutated through the ``set_owned`` / ``clear_owned``
helpers so call sites never hold a live reference into the store.
"""

from __future__ import annotations

# The owning-lane key for the FOREGROUND chat agent's working tab.
AGENT_KEY = "agent"

# user_id -> {target_key: target_id}. ``None`` user ids are namespaced under
# the literal key "" so a not-yet-bound visible backend can't collide with a
# real user.
_registry: dict[str, dict[str, str]] = {}

# user_id -> {target_id, ...} : agent tabs the agent itself CREATED (vs
# borrowed). ONLY ids in here are eligible for the turn-end auto-close. A
# borrowed (pre-existing) user tab is never recorded here, so it can never be
# closed by agent logic. Kept PARALLEL to ``_registry`` (not a value flag) so
# the existing watcher/background callers and tests stay byte-for-byte
# unchanged.
_agent_created: dict[str, set[str]] = {}


def _norm(user_id: str | None) -> str:
    return user_id or ""


def set_owned(user_id: str | None, target_key: str, target_id: str) -> None:
    """Record that *target_id* is the tab owned by *target_key* for this user."""
    bucket = _registry.setdefault(_norm(user_id), {})
    bucket[target_key] = target_id


def get_owned(user_id: str | None, target_key: str) -> str | None:
    """Return the owned tab id for *target_key*, or ``None`` if unset."""
    return _registry.get(_norm(user_id), {}).get(target_key)


def clear_owned(user_id: str | None, target_key: str | None = None) -> None:
    """Forget one owned tab (``target_key``) or ALL of a user's owned tabs.

    Safe to call when nothing is registered (no-op).
    """
    bucket = _registry.get(_norm(user_id))
    if bucket is None:
        return
    if target_key is None:
        _registry.pop(_norm(user_id), None)
        return
    bucket.pop(target_key, None)
    if not bucket:
        _registry.pop(_norm(user_id), None)


def all_owned_target_ids(user_id: str | None) -> frozenset[str]:
    """Every tab id owned by any lane for this user (empty if none).

    Includes the ``"agent"`` tab — used by the VISIBLE backend's MRU pick so a
    second visible turn doesn't accidentally grab a watcher/background tab. The
    REAPER must NOT anchor the agent tab, so it reads
    :func:`anchored_target_ids_excluding_agent` instead.
    """
    return frozenset(_registry.get(_norm(user_id), {}).values())


def anchored_target_ids_excluding_agent(user_id: str | None) -> frozenset[str]:
    """Owned tab ids the REAPER must keep open — watcher/background only.

    The agent's working tab (key :data:`AGENT_KEY`) is deliberately EXCLUDED:
    it must stay idle-reapable so a tab the turn-end auto-close missed (crash,
    timeout, degraded path) gets cleaned up by the heartbeat reaper instead of
    leaking forever. Watcher (``watch:*``) and ``background`` tabs stay
    anchored — closing those breaks a live monitoring contract.
    """
    bucket = _registry.get(_norm(user_id), {})
    return frozenset(
        tid for key, tid in bucket.items() if key != AGENT_KEY
    )


# ── agent-created vs borrowed bookkeeping (SAFETY-CRITICAL) ───────────


def mark_agent_created(user_id: str | None, target_id: str) -> None:
    """Record that *target_id* is an agent tab the agent itself CREATED.

    ONLY ids marked here are eligible for the turn-end auto-close. Never call
    this for a BORROWED (pre-existing) user tab.
    """
    if not target_id:
        return
    _agent_created.setdefault(_norm(user_id), set()).add(target_id)


def is_agent_created(user_id: str | None, target_id: str | None) -> bool:
    """True iff *target_id* is an agent tab the agent CREATED (not borrowed)."""
    if not target_id:
        return False
    return target_id in _agent_created.get(_norm(user_id), set())


def clear_agent_created(
    user_id: str | None, target_id: str | None = None
) -> None:
    """Forget one created-tab id, or ALL of a user's created-tab ids.

    Safe to call when nothing is recorded (no-op).
    """
    bucket = _agent_created.get(_norm(user_id))
    if bucket is None:
        return
    if target_id is None:
        _agent_created.pop(_norm(user_id), None)
        return
    bucket.discard(target_id)
    if not bucket:
        _agent_created.pop(_norm(user_id), None)


def should_close_agent_tab(
    *,
    created_by_agent: bool,
    target_id: str | None,
    total_tabs: int,
    watcher_target_ids: frozenset[str] | set[str],
) -> bool:
    """Pure decision: should the foreground agent close its tab at turn-end?

    Returns ``True`` ONLY when EVERY guard passes:
      * ``created_by_agent`` — a BORROWED tab is NEVER closed (the inviolable
        safety invariant — protects the user's signed-in Upwork/etc. tab);
      * ``target_id`` is truthy — there is still an agent-owned id to close;
      * ``target_id`` is NOT a watcher/background-owned id — defense-in-depth
        so agent logic can never close a monitoring tab even on a key clash;
      * ``total_tabs > 1`` — never close the only open tab (would leave the
        user's window empty).

    No browser access — unit-testable in isolation.
    """
    if not created_by_agent:
        return False
    if not target_id:
        return False
    if target_id in (watcher_target_ids or frozenset()):
        return False
    if total_tabs <= 1:
        return False
    return True
