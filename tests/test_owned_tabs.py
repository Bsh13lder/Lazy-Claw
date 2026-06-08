"""Tests for the process-global owned-tab registry.

The registry maps ``user_id -> {target_key: target_id}`` so the foreground
(visible) CDP backend can EXCLUDE background/watcher-owned tabs from its MRU
pick, and the tab reaper can ANCHOR them. It is module-global (not backend
state) so it survives the daemon's per-tick backend rebuilds.
"""

from __future__ import annotations

import pytest

from lazyclaw.browser import owned_tabs


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts from an empty registry."""
    owned_tabs._registry.clear()
    yield
    owned_tabs._registry.clear()


def test_set_and_get_roundtrip():
    owned_tabs.set_owned("user-a", "background", "TARGET_1")
    assert owned_tabs.get_owned("user-a", "background") == "TARGET_1"


def test_get_missing_returns_none():
    assert owned_tabs.get_owned("user-a", "background") is None
    owned_tabs.set_owned("user-a", "background", "T1")
    assert owned_tabs.get_owned("user-a", "watch:job9") is None


def test_set_overwrites_same_key():
    owned_tabs.set_owned("user-a", "background", "OLD")
    owned_tabs.set_owned("user-a", "background", "NEW")
    assert owned_tabs.get_owned("user-a", "background") == "NEW"
    # No duplicate accumulation in the all-ids view.
    assert owned_tabs.all_owned_target_ids("user-a") == frozenset({"NEW"})


def test_all_owned_target_ids_collects_every_key():
    owned_tabs.set_owned("user-a", "background", "BG")
    owned_tabs.set_owned("user-a", "watch:job1", "W1")
    owned_tabs.set_owned("user-a", "watch:job2", "W2")
    assert owned_tabs.all_owned_target_ids("user-a") == frozenset(
        {"BG", "W1", "W2"}
    )


def test_users_are_isolated():
    owned_tabs.set_owned("user-a", "background", "A_BG")
    owned_tabs.set_owned("user-b", "background", "B_BG")
    assert owned_tabs.all_owned_target_ids("user-a") == frozenset({"A_BG"})
    assert owned_tabs.all_owned_target_ids("user-b") == frozenset({"B_BG"})
    assert owned_tabs.get_owned("user-b", "background") == "B_BG"


def test_all_owned_target_ids_empty_user():
    assert owned_tabs.all_owned_target_ids("nobody") == frozenset()


def test_clear_one_key():
    owned_tabs.set_owned("user-a", "background", "BG")
    owned_tabs.set_owned("user-a", "watch:job1", "W1")
    owned_tabs.clear_owned("user-a", "background")
    assert owned_tabs.get_owned("user-a", "background") is None
    assert owned_tabs.get_owned("user-a", "watch:job1") == "W1"


def test_clear_all_for_user():
    owned_tabs.set_owned("user-a", "background", "BG")
    owned_tabs.set_owned("user-a", "watch:job1", "W1")
    owned_tabs.clear_owned("user-a")
    assert owned_tabs.all_owned_target_ids("user-a") == frozenset()


def test_clear_missing_is_noop():
    # Must not raise even when nothing is registered.
    owned_tabs.clear_owned("ghost")
    owned_tabs.clear_owned("ghost", "background")


def test_none_user_id_is_safe():
    # The visible backend may have user_id=None before late-binding.
    assert owned_tabs.all_owned_target_ids(None) == frozenset()
    # Writing under None must not crash and must not leak into other users.
    owned_tabs.set_owned(None, "background", "X")
    assert owned_tabs.get_owned(None, "background") == "X"
    assert owned_tabs.all_owned_target_ids("user-a") == frozenset()
