"""Tests for lazyclaw/runtime/instant_dispatch.py.

Two concerns drive these tests:
  1. Positive: every documented phrasing for a route MUST match it.
  2. Negative: similar-but-different phrasings MUST fall through to the
     full brain path (return None). False positives are worse than false
     negatives — a wrong instant-dispatch ships a partial answer with no
     LLM correction.
"""
from __future__ import annotations

import pytest

from lazyclaw.runtime.instant_dispatch import (
    INSTANT_ROUTES,
    InstantDispatchResult,
    try_instant_dispatch,
)


# ── Fakes ────────────────────────────────────────────────────────────


class FakeSkill:
    """Minimal stand-in for a BaseSkill — only execute() is exercised."""

    def __init__(self, output: str = "OK", raises: Exception | None = None) -> None:
        self._output = output
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, user_id: str, params: dict) -> str:
        self.calls.append((user_id, dict(params)))
        if self._raises is not None:
            raise self._raises
        return self._output


class FakeRegistry:
    def __init__(self, skills: dict[str, FakeSkill] | None = None) -> None:
        self._skills = skills or {}

    def get(self, name: str):
        return self._skills.get(name)


def _registry_with_all_routes() -> FakeRegistry:
    skills = {
        route.skill_name: FakeSkill(output=f"<{route.skill_name} output>")
        for route in INSTANT_ROUTES
    }
    return FakeRegistry(skills)


# ── Positive matches per route ───────────────────────────────────────


@pytest.mark.parametrize("phrasing", [
    "check upwork inbox",
    "Check Upwork Inbox",
    "check my upwork inbox",
    "check the upwork inbox",
    "check upwork inbox now",
    "sweep upwork dms",
    "scan upwork messages",
    "read my upwork dms",
    "sync upwork chats",
    "refresh upwork conversations",
    "please check upwork inbox",
    "can you check my upwork inbox",
    "check upwork inbox.",
    "check upwork inbox!",
    "check upwork inbox?",
    "check upwork inbox now.",
    "check upwork dm",
    "check upwork convos",
])
@pytest.mark.asyncio
async def test_upwork_inbox_positive_matches(phrasing: str) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is not None, f"{phrasing!r} should match upwork_inbox_check"
    assert result.skill_name == "upwork_inbox_check"


@pytest.mark.parametrize("phrasing", [
    "show jobs",
    "list jobs",
    "show my jobs",
    "list my reminders",
    "show all scheduled jobs",
    "show active jobs",
    "show cron jobs",
    "show me my jobs",
    "view current reminders",
    "Display All Reminders",
    "get jobs",
    "list reminders.",
])
@pytest.mark.asyncio
async def test_list_jobs_positive_matches(phrasing: str) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is not None, f"{phrasing!r} should match list_jobs"
    assert result.skill_name == "list_jobs"


@pytest.mark.parametrize("phrasing", [
    "show tasks",
    "show task",
    "show my tasks",
    "list tasks",
    "view active tasks",
    "show open tasks",
    "show pending tasks",
    "display my tasks",
    "list all tasks",
])
@pytest.mark.asyncio
async def test_list_tasks_positive_matches(phrasing: str) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is not None, f"{phrasing!r} should match list_tasks"
    assert result.skill_name == "list_tasks"


@pytest.mark.parametrize("phrasing", [
    "show contacts",
    "show contact",
    "list contacts",
    "show my contacts",
    "list all contacts",
    "view contacts",
])
@pytest.mark.asyncio
async def test_list_contacts_positive_matches(phrasing: str) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is not None, f"{phrasing!r} should match list_contacts"
    assert result.skill_name == "list_contacts"


# ── Critical negative cases (must fall through) ──────────────────────


@pytest.mark.parametrize("phrasing", [
    # Embedded in a longer sentence
    "i should show jobs later",
    "remind me to show tasks tomorrow",
    "after you check upwork inbox tell me",
    # Action verbs that should go to the brain
    "send a message on upwork",
    "reply to the upwork dm from john",
    "delete the cron job called sweep",
    "edit my upwork inbox cron",
    # Different intent — needs brain reasoning
    "show jobs from upwork",  # — search jobs on the platform
    "find jobs",
    "search jobs",
    "complete task buy milk",
    "add a task: pay rent",
    "save contact john +34 555 12 34 56",
    "find contact john",
    # Compound
    "show jobs and check upwork inbox",
    # Greetings & chat
    "hi",
    "ok",
    "thanks",
    # Empty / whitespace
    "",
    "   ",
    # Plan mode triggers should NOT shortcut
    "plan a marketing strategy for upwork dms",
])
@pytest.mark.asyncio
async def test_negative_matches_fall_through(phrasing: str) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is None, f"{phrasing!r} should NOT match any instant route"


# ── Whole-message anchor enforcement ─────────────────────────────────


@pytest.mark.asyncio
async def test_does_not_match_when_suffix_is_extra_words() -> None:
    reg = _registry_with_all_routes()
    # If the user adds context beyond punctuation, defer to the brain.
    assert await try_instant_dispatch(
        "show jobs and tell me which are stale", reg, "u1",
    ) is None
    assert await try_instant_dispatch(
        "check upwork inbox and then email me a summary", reg, "u1",
    ) is None


@pytest.mark.asyncio
async def test_does_not_match_when_prefix_is_extra_words() -> None:
    reg = _registry_with_all_routes()
    assert await try_instant_dispatch(
        "ok now show jobs", reg, "u1",
    ) is None
    assert await try_instant_dispatch(
        "actually, list my tasks", reg, "u1",
    ) is None


# ── Graceful degradation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_none_when_registry_is_none() -> None:
    assert await try_instant_dispatch("show jobs", None, "u1") is None


@pytest.mark.asyncio
async def test_returns_none_when_user_id_missing() -> None:
    reg = _registry_with_all_routes()
    assert await try_instant_dispatch("show jobs", reg, "") is None


@pytest.mark.asyncio
async def test_falls_through_when_skill_missing_from_registry() -> None:
    # Empty registry — pattern matches but skill not registered.
    reg = FakeRegistry({})
    result = await try_instant_dispatch("show jobs", reg, "u1")
    assert result is None


@pytest.mark.asyncio
async def test_falls_through_when_skill_raises() -> None:
    skill = FakeSkill(raises=RuntimeError("boom"))
    reg = FakeRegistry({"list_jobs": skill})
    result = await try_instant_dispatch("show jobs", reg, "u1")
    assert result is None
    assert len(skill.calls) == 1  # confirms it actually fired


@pytest.mark.asyncio
async def test_falls_through_when_skill_returns_non_string() -> None:
    class NonStringSkill:
        async def execute(self, user_id: str, params: dict):
            return {"not": "a string"}

    reg = FakeRegistry({"list_jobs": NonStringSkill()})  # type: ignore[dict-item]
    result = await try_instant_dispatch("show jobs", reg, "u1")
    assert result is None


# ── Success path — output + skill + timing surfaced ─────────────────


@pytest.mark.asyncio
async def test_successful_dispatch_returns_output_and_skill() -> None:
    skill = FakeSkill(output="Scheduled jobs (2):\n  - cron-a\n  - reminder-b")
    reg = FakeRegistry({"list_jobs": skill})
    result = await try_instant_dispatch("show jobs", reg, "u1")
    assert isinstance(result, InstantDispatchResult)
    assert result.output.startswith("Scheduled jobs")
    assert result.skill_name == "list_jobs"
    assert result.elapsed_ms >= 0
    assert result.route_description == "list jobs / reminders"
    assert skill.calls == [("u1", {})]


@pytest.mark.asyncio
async def test_params_passed_as_empty_dict_for_no_param_routes() -> None:
    skill = FakeSkill(output="no contacts")
    reg = FakeRegistry({"list_contacts": skill})
    await try_instant_dispatch("show contacts", reg, "user-xyz")
    assert skill.calls == [("user-xyz", {})]


# ── Cron / heartbeat prefix tolerance ────────────────────────────────


@pytest.mark.parametrize("phrasing, expected_skill", [
    # Real-world heartbeat shapes from production logs
    ("[JOB:survival_message_check] check my upwork inbox",
     "upwork_inbox_check"),
    ("[JOB:survival_message_check] check upwork inbox now",
     "upwork_inbox_check"),
    ("[REMINDER] show jobs",
     "list_jobs"),
    ("[TASK_REMINDER:abc-123] list my tasks",
     "list_tasks"),
    ("[WATCHER] show contacts",
     "list_contacts"),
    ("[MCP_WATCHER] check my upwork dms",
     "upwork_inbox_check"),
    # Extra whitespace and casing
    ("  [JOB:foo]   check upwork inbox  ",
     "upwork_inbox_check"),
    ("[JOB:Survival_Message_Check] CHECK UPWORK INBOX",
     "upwork_inbox_check"),
])
@pytest.mark.asyncio
async def test_known_cron_prefixes_are_stripped_before_matching(
    phrasing: str, expected_skill: str,
) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is not None, (
        f"{phrasing!r} should hit {expected_skill} after prefix strip"
    )
    assert result.skill_name == expected_skill


@pytest.mark.parametrize("phrasing", [
    # Unknown prefixes must NOT be stripped — the brain has to interpret
    # them. Forcing instant dispatch would mask whatever the system meant.
    "[CUSTOM_HOOK] check upwork inbox",
    "[NOTIFICATION] show jobs",
    "[OTHER:abc] show tasks",
    "[X] show contacts",
    # Multiple prefixes only the first known one is stripped; anything
    # after that is part of the message proper, so the route should miss.
    "[JOB:x] [REMINDER] show jobs",
])
@pytest.mark.asyncio
async def test_unknown_prefix_falls_through(phrasing: str) -> None:
    reg = _registry_with_all_routes()
    result = await try_instant_dispatch(phrasing, reg, "u1")
    assert result is None, f"{phrasing!r} must NOT match any route"


@pytest.mark.asyncio
async def test_cron_prefix_alone_still_misses() -> None:
    # Just the prefix, no instruction body — must fall through.
    reg = _registry_with_all_routes()
    assert await try_instant_dispatch("[JOB:x]", reg, "u1") is None
    assert await try_instant_dispatch("[REMINDER]", reg, "u1") is None


@pytest.mark.asyncio
async def test_only_first_matching_route_fires() -> None:
    # Build a registry where the message would match list_jobs and the
    # upwork skill is also present; ensure list_jobs fires (it appears
    # later in INSTANT_ROUTES than upwork, but only one matches the
    # phrasing).
    upwork = FakeSkill(output="upwork")
    jobs = FakeSkill(output="jobs")
    reg = FakeRegistry({"upwork_inbox_check": upwork, "list_jobs": jobs})
    result = await try_instant_dispatch("show jobs", reg, "u1")
    assert result is not None and result.skill_name == "list_jobs"
    assert len(upwork.calls) == 0
    assert len(jobs.calls) == 1
