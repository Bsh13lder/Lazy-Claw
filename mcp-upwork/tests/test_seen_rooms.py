"""Unit tests for the seen-rooms tracker that drives is_new in
upwork_get_messages.

The tracker persists in PROFILE_DIR / lazyclaw_seen_rooms.json. On first
sweep every room is `is_new=True`; subsequent sweeps only mark NEW
room_ids as new. Drives the founder-contact owner_offer in
upwork_inbox_check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_profile_dir(tmp_path, monkeypatch):
    """Point the messages module's PROFILE_DIR at a fresh tmp dir."""
    monkeypatch.setenv("LAZYCLAW_BROWSER_PROFILE_DIR", str(tmp_path))
    # Force re-import so PROFILE_DIR is recomputed from the env var.
    import importlib

    import upwork_mcp.browser.client as client_mod
    import upwork_mcp.tools.messages as messages_mod

    importlib.reload(client_mod)
    importlib.reload(messages_mod)
    return tmp_path, messages_mod


def test_load_seen_rooms_returns_empty_set_when_file_missing(isolated_profile_dir):
    _, messages_mod = isolated_profile_dir
    assert messages_mod._load_seen_rooms() == set()


def test_save_then_load_round_trips(isolated_profile_dir):
    tmp_path, messages_mod = isolated_profile_dir
    messages_mod._save_seen_rooms({"r1", "r2", "r3"})
    assert messages_mod._load_seen_rooms() == {"r1", "r2", "r3"}


def test_seen_rooms_file_is_json_list(isolated_profile_dir):
    tmp_path, messages_mod = isolated_profile_dir
    messages_mod._save_seen_rooms({"a", "b"})
    path = messages_mod._seen_rooms_path()
    raw = json.loads(path.read_text())
    assert sorted(raw) == ["a", "b"]


def test_save_creates_parent_dir(tmp_path, monkeypatch):
    """If PROFILE_DIR doesn't exist yet (first-ever run), save_seen_rooms
    creates it instead of raising."""
    nested = tmp_path / "does-not-exist-yet"
    monkeypatch.setenv("LAZYCLAW_BROWSER_PROFILE_DIR", str(nested))
    import importlib

    import upwork_mcp.browser.client as client_mod
    import upwork_mcp.tools.messages as messages_mod

    importlib.reload(client_mod)
    importlib.reload(messages_mod)

    messages_mod._save_seen_rooms({"r1"})
    assert (nested / "lazyclaw_seen_rooms.json").exists()
    assert messages_mod._load_seen_rooms() == {"r1"}


def test_load_seen_rooms_returns_empty_set_on_corrupt_json(isolated_profile_dir):
    """Corrupt JSON must NOT raise — falls back to empty set so the
    inbox sweep still works (every conv becomes is_new=True for one
    sweep, then the file gets rewritten clean)."""
    tmp_path, messages_mod = isolated_profile_dir
    path = messages_mod._seen_rooms_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json {{{")
    assert messages_mod._load_seen_rooms() == set()


def test_load_seen_rooms_returns_empty_set_when_file_is_object(isolated_profile_dir):
    """Defensive: an old / migrated file could be a JSON object instead
    of a list. Don't crash — treat as empty and rebuild."""
    tmp_path, messages_mod = isolated_profile_dir
    path = messages_mod._seen_rooms_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"r1": True}))
    assert messages_mod._load_seen_rooms() == set()


def test_seen_rooms_path_is_under_profile_dir(isolated_profile_dir):
    tmp_path, messages_mod = isolated_profile_dir
    path = messages_mod._seen_rooms_path()
    # PROFILE_DIR resolves to tmp_path because of the env var.
    assert Path(tmp_path) in path.parents or path.parent == Path(tmp_path)
    assert path.name == "lazyclaw_seen_rooms.json"
