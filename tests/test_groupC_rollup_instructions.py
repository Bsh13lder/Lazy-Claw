"""BUG C1 — test that _WEEKLY_INSTRUCTION and _MONTHLY_INSTRUCTION use
`period_id=` (the canonical skill parameter) and NOT `week_id=` as the
keyword argument passed to lazybrain_mark_rolled_up.

These are pure-string assertions — no DB, no async, no mocks.
"""
from __future__ import annotations

from lazyclaw.lazybrain.rollup import _MONTHLY_INSTRUCTION, _WEEKLY_INSTRUCTION


def test_weekly_instruction_uses_period_id() -> None:
    """The cron instruction must tell the agent to pass `period_id=`, not `week_id=`."""
    assert "period_id=" in _WEEKLY_INSTRUCTION, (
        "_WEEKLY_INSTRUCTION must use `period_id=` when calling "
        "lazybrain_mark_rolled_up — `week_id=` is not a valid skill parameter"
    )


def test_weekly_instruction_does_not_use_week_id_kwarg() -> None:
    """Confirm the old broken kwarg name is gone from the weekly instruction."""
    assert "week_id=" not in _WEEKLY_INSTRUCTION, (
        "_WEEKLY_INSTRUCTION still contains `week_id=` — this will silently "
        "fail because MarkRolledUpSkill only accepts `period_id`"
    )


def test_monthly_instruction_uses_period_id() -> None:
    """The monthly cron instruction must also use `period_id=`."""
    assert "period_id=" in _MONTHLY_INSTRUCTION, (
        "_MONTHLY_INSTRUCTION must use `period_id=` when calling "
        "lazybrain_mark_rolled_up"
    )


def test_monthly_instruction_does_not_use_week_id_kwarg() -> None:
    """Confirm `week_id=` (the broken monthly kwarg) is removed."""
    assert "week_id=" not in _MONTHLY_INSTRUCTION, (
        "_MONTHLY_INSTRUCTION still contains `week_id=` — monthly rollups "
        "use period labels like M2026-05, not week labels"
    )


def test_weekly_instruction_mentions_mark_rolled_up() -> None:
    """Sanity-check: the instruction still actually calls the skill."""
    assert "lazybrain_mark_rolled_up" in _WEEKLY_INSTRUCTION


def test_monthly_instruction_mentions_mark_rolled_up() -> None:
    assert "lazybrain_mark_rolled_up" in _MONTHLY_INSTRUCTION
