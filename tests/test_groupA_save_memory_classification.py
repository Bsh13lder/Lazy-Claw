"""Tests: save_memory maps legacy memory_type strings to taxonomy values.

Regression guard for the path where personal.save_memory is called with
legacy type names ('preference', 'context', 'fact') and must pass the
correctly mapped memory_type kwarg through to lb_store.save_note so the
taxonomy classifier's explicit-kwarg-wins rule fires.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_fake_config(memory_unified: bool = True):
    cfg = MagicMock()
    cfg.memory_unified = memory_unified
    return cfg


def _make_fake_note(memory_type: str = "fact") -> dict:
    return {
        "id": "lb:test-note-id",
        "title": "Test note",
        "tags": ["memory", "auto", "owner/user", f"kind/{memory_type}"],
        "memory_type": memory_type,
    }


# ─── Parametrized mapping tests ───────────────────────────────────────────────

@pytest.mark.parametrize("input_type,expected_type", [
    ("preference", "user"),
    ("context", "project"),
    ("fact", "fact"),
    ("unknown_legacy_type", "fact"),
    ("random_string", "fact"),
])
@pytest.mark.asyncio
async def test_save_memory_maps_legacy_types(input_type, expected_type):
    """save_memory maps legacy type → taxonomy value passed to lb_store.save_note."""
    config = _make_fake_config(memory_unified=True)
    fake_note = _make_fake_note(expected_type)

    captured_kwargs = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured_kwargs["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.memory.personal.get_user_dek", new=AsyncMock(return_value=b"k" * 32)),
        patch("lazyclaw.lazybrain.store.save_note", new=fake_save_note),
        patch("lazyclaw.lazybrain.events.publish_note_saved"),
    ):
        from lazyclaw.memory.personal import save_memory
        await save_memory(config, "user1", "test content", memory_type=input_type)

    assert captured_kwargs.get("memory_type") == expected_type, (
        f"input '{input_type}' should map to '{expected_type}', "
        f"got '{captured_kwargs.get('memory_type')}'"
    )


@pytest.mark.asyncio
async def test_save_memory_default_type_is_fact():
    """save_memory called with default memory_type='fact' passes 'fact' to lb_store."""
    config = _make_fake_config(memory_unified=True)
    fake_note = _make_fake_note("fact")

    captured_kwargs = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured_kwargs["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.memory.personal.get_user_dek", new=AsyncMock(return_value=b"k" * 32)),
        patch("lazyclaw.lazybrain.store.save_note", new=fake_save_note),
        patch("lazyclaw.lazybrain.events.publish_note_saved"),
    ):
        from lazyclaw.memory.personal import save_memory
        await save_memory(config, "user1", "test content")

    assert captured_kwargs.get("memory_type") == "fact"


@pytest.mark.asyncio
async def test_save_memory_preference_maps_to_user():
    """Dedicated test: 'preference' legacy type → 'user' taxonomy."""
    config = _make_fake_config(memory_unified=True)
    fake_note = _make_fake_note("user")
    captured = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.memory.personal.get_user_dek", new=AsyncMock(return_value=b"k" * 32)),
        patch("lazyclaw.lazybrain.store.save_note", new=fake_save_note),
        patch("lazyclaw.lazybrain.events.publish_note_saved"),
    ):
        from lazyclaw.memory.personal import save_memory
        await save_memory(config, "user1", "prefers dark mode", memory_type="preference")

    assert captured["memory_type"] == "user"


@pytest.mark.asyncio
async def test_save_memory_context_maps_to_project():
    """Dedicated test: 'context' legacy type → 'project' taxonomy."""
    config = _make_fake_config(memory_unified=True)
    fake_note = _make_fake_note("project")
    captured = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.memory.personal.get_user_dek", new=AsyncMock(return_value=b"k" * 32)),
        patch("lazyclaw.lazybrain.store.save_note", new=fake_save_note),
        patch("lazyclaw.lazybrain.events.publish_note_saved"),
    ):
        from lazyclaw.memory.personal import save_memory
        await save_memory(config, "user1", "project is phase 3", memory_type="context")

    assert captured["memory_type"] == "project"
