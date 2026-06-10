"""_last_watcher_context must stay bounded — it's a module-level dict keyed by
user_id that previously grew forever in multi-user deployments (2026-06-10
security review, finding L3)."""

from __future__ import annotations

from lazyclaw.heartbeat import daemon


def setup_function() -> None:
    daemon._last_watcher_context.clear()


def teardown_function() -> None:
    daemon._last_watcher_context.clear()


def test_store_and_get_round_trip() -> None:
    daemon._store_watcher_context("u1", "whatsapp", [{"id": 1}], "1 new message")
    ctx = daemon.get_last_watcher_context("u1")
    assert ctx is not None
    assert ctx["service"] == "whatsapp"
    assert ctx["items"] == [{"id": 1}]


def test_context_dict_bounded_across_users() -> None:
    """Adding more users than the cap must evict the oldest entry instead of
    growing without bound."""
    cap = daemon._MAX_WATCHER_CONTEXT_USERS
    for i in range(cap + 10):
        daemon._store_watcher_context(f"user-{i}", "email", [], f"note {i}")
    assert len(daemon._last_watcher_context) <= cap
    # the most recent user is always retained
    assert daemon.get_last_watcher_context(f"user-{cap + 9}") is not None


def test_existing_user_update_does_not_evict() -> None:
    """Re-storing for an already-tracked user must not evict anyone."""
    cap = daemon._MAX_WATCHER_CONTEXT_USERS
    for i in range(cap):
        daemon._store_watcher_context(f"user-{i}", "email", [], f"note {i}")
    daemon._store_watcher_context("user-0", "email", [], "updated")
    assert len(daemon._last_watcher_context) == cap
    ctx = daemon.get_last_watcher_context("user-0")
    assert ctx is not None and ctx["notification"] == "updated"
