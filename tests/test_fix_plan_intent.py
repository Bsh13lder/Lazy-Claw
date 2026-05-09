"""Intent detection + bypass valves for plan-mode fix flow.

The detector is the cheap upstream guard that decides whether a message
goes through the plan-mode gate or straight to the brain. False positives
are recoverable (the user can tap Cancel or prefix ``!``); false
negatives mean we silently skip plan-mode, which is fine for routine
"add task" / question requests.
"""
from __future__ import annotations

import pytest

from lazyclaw.runtime import fix_plan


# ── Verb detection ─────────────────────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "fix the smart_intake bug where deadlines come back UTC",
    "Refactor the heartbeat daemon",
    "please upgrade the brain to Sonnet",
    "rebuild the index",
    "improve the survival skills",
    "optimize the build",
    # Spanish equivalents
    "arregla el bug de las deadlines",
    "mejora el código del agente",
])
def test_detect_fix_intent_triggers_on_verbs(msg: str) -> None:
    assert fix_plan.detect_fix_intent(msg) is True


@pytest.mark.parametrize("msg", [
    "add a task to call the bank tomorrow",
    "what's the weather?",
    "show me my tasks",
    "list jobs",
    "remind me to walk the dog",
    "",
    "   ",
])
def test_detect_fix_intent_ignores_neutral(msg: str) -> None:
    assert fix_plan.detect_fix_intent(msg) is False


# ── Bypass prefixes ────────────────────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "!fix it",
    "! fix the typo",
    "just do fix the bug",
    "go ahead refactor it",
    "ya hazlo arreglar el bug",
])
def test_bypass_prefixes_skip_plan_mode(msg: str) -> None:
    assert fix_plan.detect_fix_intent(msg) is False


# ── Recently-approved cache ────────────────────────────────────────────


def test_recently_approved_dedups_within_24h() -> None:
    user_id = "u-test-1"
    msg = "fix the smart_intake bug where deadlines come back UTC"
    assert fix_plan.is_recently_approved(user_id, msg) is False
    fix_plan.mark_approved(user_id, msg)
    assert fix_plan.is_recently_approved(user_id, msg) is True


def test_recently_approved_different_user_independent() -> None:
    msg = "fix the recurring offset"
    fix_plan.mark_approved("u-A", msg)
    assert fix_plan.is_recently_approved("u-A", msg) is True
    assert fix_plan.is_recently_approved("u-B", msg) is False


def test_slug_stable_for_same_message() -> None:
    a = fix_plan._slug_for("Fix the smart_intake bug")
    b = fix_plan._slug_for("fix the SMART_INTAKE bug")
    assert a == b


# ── FixPlan markdown rendering ─────────────────────────────────────────


def test_fix_plan_markdown_skips_empty_sections() -> None:
    plan = fix_plan.FixPlan(
        summary="Upgrade tasker tz handling.",
        steps=["A", "B"],
        questions=[],
        risks=[],
        confidence="high",
    )
    md = plan.to_markdown()
    assert "Upgrade tasker tz handling." in md
    assert "**Plan:**" in md
    assert "1. A" in md
    assert "2. B" in md
    assert "**Questions:**" not in md
    assert "**Risks:**" not in md
    assert "Confidence: high" in md


def test_fix_plan_markdown_renders_all_sections() -> None:
    plan = fix_plan.FixPlan(
        summary="x",
        steps=["s1"],
        questions=["q1"],
        risks=["r1"],
        confidence="medium",
    )
    md = plan.to_markdown()
    for needle in ("Plan:", "Questions:", "Risks:", "1. s1", "• q1", "• r1"):
        assert needle in md
