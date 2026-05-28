"""BUG C1b — MarkRolledUpSkill must accept `week_id` and `month_id` as
backward-compat aliases for `period_id`.

Strategy: mock out `store.get_note` and `store.update_note` so the test
stays in-process without a real DB. The skill's execute() is the only
code-under-test.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.skills.builtin.lazybrain.rollup import MarkRolledUpSkill

# asyncio mark applied selectively per-test (not module-wide) so the sync
# schema test doesn't trigger a PytestWarning about non-async functions.

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER_ID = "test-user-alias"
_SOURCE_ID = "note-abc123"

# A minimal note dict returned by store.get_note.
_FAKE_NOTE = {
    "id": _SOURCE_ID,
    "title": "Some note",
    "tags": ["my-tag"],
    "content": "body",
    "updated_at": "2026-05-27T00:00:00",
    "created_at": "2026-05-27T00:00:00",
}

# The updated note returned by store.update_note.
_UPDATED_NOTE = {**_FAKE_NOTE, "tags": ["my-tag", "rolled-up", "source-of/W23-2026", "in-rollup/r1"]}


def _make_skill() -> MarkRolledUpSkill:
    return MarkRolledUpSkill(config=None)  # config unused — store is mocked


# ---------------------------------------------------------------------------
# Helper context manager: patch both store calls in one place
# ---------------------------------------------------------------------------

def _patched_store(updated_note: dict | None = None):
    """Context manager that stubs store.get_note + store.update_note."""
    # Default updated_note adapts to the period_id in params, good enough.
    if updated_note is None:
        updated_note = {**_FAKE_NOTE, "tags": list(_FAKE_NOTE["tags"]) + ["rolled-up"]}
    return (
        patch(
            "lazyclaw.skills.builtin.lazybrain.rollup.store.get_note",
            new_callable=AsyncMock,
            return_value=_FAKE_NOTE,
        ),
        patch(
            "lazyclaw.skills.builtin.lazybrain.rollup.store.update_note",
            new_callable=AsyncMock,
            return_value=updated_note,
        ),
        patch(
            "lazyclaw.skills.builtin.lazybrain.rollup.events.publish_note_saved",
        ),
    )


# ---------------------------------------------------------------------------
# Test: canonical `period_id` still works
# ---------------------------------------------------------------------------

async def test_period_id_canonical_succeeds() -> None:
    """Using the documented `period_id` parameter must mark the note."""
    skill = _make_skill()
    params = {
        "source_ids": [_SOURCE_ID],
        "period_id": "W23-2026",
        "rollup_id": "r1",
    }
    with _patched_store()[0], _patched_store()[1], _patched_store()[2]:
        # Re-enter all three patches properly via nested with:
        pass

    # Proper nested patching:
    with patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.get_note",
        new_callable=AsyncMock,
        return_value=_FAKE_NOTE,
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.update_note",
        new_callable=AsyncMock,
        return_value={**_FAKE_NOTE, "tags": ["my-tag", "rolled-up", "source-of/W23-2026", "in-rollup/r1"]},
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.events.publish_note_saved",
    ):
        result = await skill.execute(_USER_ID, params)

    assert "❌" not in result, f"Expected success, got: {result}"
    assert "1" in result or "Marked" in result


# ---------------------------------------------------------------------------
# Test: `week_id` alias succeeds (BUG C1b fix)
# ---------------------------------------------------------------------------

async def test_week_id_alias_succeeds() -> None:
    """Stale cron instructions passing `week_id=` must NOT fail silently."""
    skill = _make_skill()
    params = {
        "source_ids": [_SOURCE_ID],
        "week_id": "W23-2026",   # alias — no `period_id` key
        "rollup_id": "r1",
    }
    with patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.get_note",
        new_callable=AsyncMock,
        return_value=_FAKE_NOTE,
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.update_note",
        new_callable=AsyncMock,
        return_value={**_FAKE_NOTE, "tags": ["my-tag", "rolled-up", "source-of/W23-2026", "in-rollup/r1"]},
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.events.publish_note_saved",
    ):
        result = await skill.execute(_USER_ID, params)

    assert "❌" not in result, (
        f"`week_id` alias must be accepted as fallback for `period_id`. Got: {result}"
    )
    assert "Marked" in result or "1" in result


# ---------------------------------------------------------------------------
# Test: `month_id` alias succeeds (BUG C1b fix)
# ---------------------------------------------------------------------------

async def test_month_id_alias_succeeds() -> None:
    """Monthly cron instructions with `month_id=` must also be accepted."""
    skill = _make_skill()
    params = {
        "source_ids": [_SOURCE_ID],
        "month_id": "M2026-05",   # alias — no `period_id` key
        "rollup_id": "r1",
    }
    with patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.get_note",
        new_callable=AsyncMock,
        return_value=_FAKE_NOTE,
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.update_note",
        new_callable=AsyncMock,
        return_value={**_FAKE_NOTE, "tags": ["my-tag", "rolled-up", "source-of/M2026-05", "in-rollup/r1"]},
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.events.publish_note_saved",
    ):
        result = await skill.execute(_USER_ID, params)

    assert "❌" not in result, (
        f"`month_id` alias must be accepted as fallback for `period_id`. Got: {result}"
    )
    assert "Marked" in result or "1" in result


# ---------------------------------------------------------------------------
# Test: missing period/week/month → required-field error
# ---------------------------------------------------------------------------

async def test_missing_all_period_fields_returns_error() -> None:
    """When none of period_id / week_id / month_id are provided, the skill
    must return the required-field error (not crash, not silently succeed)."""
    skill = _make_skill()
    params = {
        "source_ids": [_SOURCE_ID],
        "rollup_id": "r1",
        # no period_id, no week_id, no month_id
    }
    with patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.get_note",
        new_callable=AsyncMock,
        return_value=_FAKE_NOTE,
    ), patch(
        "lazyclaw.skills.builtin.lazybrain.rollup.store.update_note",
        new_callable=AsyncMock,
        return_value=_FAKE_NOTE,
    ):
        result = await skill.execute(_USER_ID, params)

    assert "❌" in result, (
        f"Missing period fields must return an error message. Got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Test: schema still lists `period_id` as canonical (not `week_id`)
# ---------------------------------------------------------------------------

def test_parameters_schema_canonical_name_is_period_id() -> None:
    """The JSON schema exposed to the agent must use `period_id`, not
    `week_id` or `month_id` — those are runtime-only aliases."""
    skill = _make_skill()
    props = skill.parameters_schema["properties"]
    assert "period_id" in props, "Schema must expose `period_id` as the canonical field"
    assert "week_id" not in props, "Schema must NOT expose `week_id` (it's a runtime alias only)"
    assert "month_id" not in props, "Schema must NOT expose `month_id` (it's a runtime alias only)"
