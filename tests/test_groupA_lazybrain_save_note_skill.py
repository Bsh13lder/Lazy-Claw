"""Tests: lazybrain_save_note skill passes memory_type kwarg to store.save_note.

Covers:
  - Explicit memory_type in params flows through to store.save_note
  - Absent memory_type lets the classifier choose (memory_type=None passed)
  - memory_type field present in parameters_schema enum
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.skills.builtin.lazybrain.notes import SaveNoteSkill


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_skill():
    cfg = MagicMock()
    return SaveNoteSkill(config=cfg)


def _make_fake_note(memory_type: str | None = None) -> dict:
    return {
        "id": "test-note-id",
        "title": "Test note",
        "tags": ["memory", "owner/agent"],
        "memory_type": memory_type,
        "pinned": False,
    }


# ─── parameters_schema includes memory_type field ────────────────────────────

def test_parameters_schema_has_memory_type_field():
    skill = _make_skill()
    schema = skill.parameters_schema
    assert "memory_type" in schema["properties"], (
        "parameters_schema must declare 'memory_type' property"
    )


def test_parameters_schema_memory_type_is_string_or_enum():
    skill = _make_skill()
    schema = skill.parameters_schema
    mt_schema = schema["properties"]["memory_type"]
    assert mt_schema.get("type") == "string", (
        "memory_type property type should be 'string'"
    )


def test_parameters_schema_memory_type_not_required():
    skill = _make_skill()
    schema = skill.parameters_schema
    required = schema.get("required", [])
    assert "memory_type" not in required, (
        "memory_type must be optional (not in required list)"
    )


def test_parameters_schema_memory_type_enum_contains_expected_values():
    skill = _make_skill()
    schema = skill.parameters_schema
    mt_schema = schema["properties"]["memory_type"]
    enum_vals = mt_schema.get("enum", [])
    # Must include at least: user, feedback, project, reference, fact
    for expected in ("user", "feedback", "project", "reference", "fact"):
        assert expected in enum_vals, (
            f"memory_type enum should include '{expected}', got {enum_vals}"
        )


# ─── execute passes memory_type kwarg when provided ──────────────────────────

@pytest.mark.asyncio
async def test_execute_passes_memory_type_to_store():
    """Explicit memory_type in params is forwarded to store.save_note."""
    skill = _make_skill()
    fake_note = _make_fake_note("feedback")
    captured = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.skills.builtin.lazybrain.notes.store") as mock_store,
        patch("lazyclaw.skills.builtin.lazybrain.notes.events"),
    ):
        mock_store.save_note = fake_save_note

        await skill.execute(
            user_id="user1",
            params={"content": "User prefers concise replies", "memory_type": "feedback"},
        )

    assert captured["memory_type"] == "feedback"


@pytest.mark.asyncio
async def test_execute_without_memory_type_passes_none_to_store():
    """Absent memory_type in params → memory_type=None passed to store (let classifier infer)."""
    skill = _make_skill()
    fake_note = _make_fake_note(None)
    captured = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.skills.builtin.lazybrain.notes.store") as mock_store,
        patch("lazyclaw.skills.builtin.lazybrain.notes.events"),
    ):
        mock_store.save_note = fake_save_note

        await skill.execute(
            user_id="user1",
            params={"content": "Some general note with no memory_type"},
        )

    # None signals "let the classifier decide" — NOT passing a default
    assert captured["memory_type"] is None


@pytest.mark.parametrize("memory_type", ["user", "feedback", "project", "reference", "fact"])
@pytest.mark.asyncio
async def test_execute_all_valid_memory_types_forwarded(memory_type):
    """Each valid memory_type value is passed through unchanged."""
    skill = _make_skill()
    fake_note = _make_fake_note(memory_type)
    captured = {}

    async def fake_save_note(cfg, user_id, content, title=None, tags=None,
                              importance=5, pinned=False, trace_session_id=None,
                              frontmatter=None, aliases=None, memory_type=None):
        captured["memory_type"] = memory_type
        return fake_note

    with (
        patch("lazyclaw.skills.builtin.lazybrain.notes.store") as mock_store,
        patch("lazyclaw.skills.builtin.lazybrain.notes.events"),
    ):
        mock_store.save_note = fake_save_note

        await skill.execute(
            user_id="user1",
            params={"content": "test content", "memory_type": memory_type},
        )

    assert captured["memory_type"] == memory_type
