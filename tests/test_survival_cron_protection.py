"""Tests for Fix C — survival-mode cron drift protection.

Two surfaces covered:

  1. EditJobSkill refuses instruction/name edits when the target job
     name is in SURVIVAL_CANONICAL_INSTRUCTIONS.
  2. The canonical-instructions table is the single source of truth used
     by both the skill guard and the heartbeat daemon's drift-restore
     pass (verified by comparing imports).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.skills.builtin.jobs import EditJobSkill
from lazyclaw.skills.builtin.survival.mode_skill import (
    SURVIVAL_CANONICAL_INSTRUCTIONS,
)


# ── Constants table ──────────────────────────────────────────────────


def test_canonical_table_contains_survival_message_check() -> None:
    # If anyone removes this entry, instant_dispatch loses the cron
    # fast-path lock and the system silently regresses to the slow path
    # on every 15-min tick. Guard it.
    assert "survival_message_check" in SURVIVAL_CANONICAL_INSTRUCTIONS
    assert SURVIVAL_CANONICAL_INSTRUCTIONS["survival_message_check"] == (
        "check my upwork inbox now"
    )


# ── EditJobSkill instruction-edit refusal ────────────────────────────


@pytest.mark.asyncio
async def test_refuses_instruction_edit_on_managed_cron() -> None:
    skill = EditJobSkill(config=object())
    fake_jobs = [{
        "id": "job-1",
        "name": "survival_message_check",
        "instruction": "check my upwork inbox now",
        "job_type": "cron",
    }]
    with (
        patch(
            "lazyclaw.heartbeat.orchestrator.list_jobs",
            AsyncMock(return_value=fake_jobs),
        ),
        patch(
            "lazyclaw.heartbeat.orchestrator.update_job",
            AsyncMock(return_value=True),
        ) as mock_update,
    ):
        result = await skill.execute("u1", {
            "job_name": "survival_message_check",
            "new_instruction": "Call upwork bot",
        })
    assert "locked" in result.lower() or "managed" in result.lower()
    assert "instruction" in result.lower()
    # Update MUST NOT have been called — the guard short-circuits.
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_refuses_name_edit_on_managed_cron() -> None:
    skill = EditJobSkill(config=object())
    fake_jobs = [{
        "id": "job-1",
        "name": "survival_message_check",
        "instruction": "check my upwork inbox now",
        "job_type": "cron",
    }]
    with (
        patch(
            "lazyclaw.heartbeat.orchestrator.list_jobs",
            AsyncMock(return_value=fake_jobs),
        ),
        patch(
            "lazyclaw.heartbeat.orchestrator.update_job",
            AsyncMock(return_value=True),
        ) as mock_update,
    ):
        result = await skill.execute("u1", {
            "job_name": "survival_message_check",
            "new_name": "renamed_cron",
        })
    assert "locked" in result.lower() or "managed" in result.lower()
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_allows_cron_expression_edit_on_managed_cron() -> None:
    # The lock is on instruction + name; cadence/cron must still be
    # editable so users can change tick frequency without bumping into
    # the survival_message_check immutability wall.
    skill = EditJobSkill(config=object())
    fake_jobs = [{
        "id": "job-1",
        "name": "survival_message_check",
        "instruction": "check my upwork inbox now",
        "job_type": "cron",
    }]
    with (
        patch(
            "lazyclaw.heartbeat.orchestrator.list_jobs",
            AsyncMock(return_value=fake_jobs),
        ),
        patch(
            "lazyclaw.heartbeat.orchestrator.update_job",
            AsyncMock(return_value=True),
        ) as mock_update,
    ):
        result = await skill.execute("u1", {
            "job_name": "survival_message_check",
            "new_cron_expression": "*/30 * * * *",
        })
    assert "Updated" in result
    mock_update.assert_called_once()
    # cron_expression should be in the patch kwargs
    assert "cron_expression" in mock_update.call_args.kwargs


@pytest.mark.asyncio
async def test_allows_instruction_edit_on_non_managed_cron() -> None:
    # Sanity: non-managed crons still editable freely.
    skill = EditJobSkill(config=object())
    fake_jobs = [{
        "id": "job-2",
        "name": "my_custom_cron",
        "instruction": "do a thing",
        "job_type": "cron",
    }]
    with (
        patch(
            "lazyclaw.heartbeat.orchestrator.list_jobs",
            AsyncMock(return_value=fake_jobs),
        ),
        patch(
            "lazyclaw.heartbeat.orchestrator.update_job",
            AsyncMock(return_value=True),
        ) as mock_update,
    ):
        result = await skill.execute("u1", {
            "job_name": "my_custom_cron",
            "new_instruction": "do a different thing",
        })
    assert "Updated" in result
    mock_update.assert_called_once()
    assert "instruction" in mock_update.call_args.kwargs
