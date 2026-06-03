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

Immutability note: reads return a fresh ``frozenset`` / plain value; the
internal dict is only mutated through the ``set_owned`` / ``clear_owned``
helpers so call sites never hold a live reference into the store.
"""

from __future__ import annotations

# user_id -> {target_key: target_id}. ``None`` user ids are namespaced under
# the literal key "" so a not-yet-bound visible backend can't collide with a
# real user.
_registry: dict[str, dict[str, str]] = {}


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
    """Every tab id owned by any lane for this user (empty if none)."""
    return frozenset(_registry.get(_norm(user_id), {}).values())
