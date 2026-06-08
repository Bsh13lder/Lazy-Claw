"""Regression tests for apply_job's SDK-first letter ladder + truthful results.

Covers the bug fixed 2026-06-02:

  * Letter generation called the Claude Code MCP FIRST. That MCP's claude
    subprocess picks up a dead ($0-balance) ANTHROPIC_API_KEY and fails with
    "Credit balance is too low" BEFORE the working EcoRouter/subscription
    path ran. Fix: EcoRouter is now the PRIMARY (and only model-backed)
    letter generator; the Claude Code MCP step is removed; template is the
    deep fallback.

  * apply_job reported "success / done" even when no real letter was
    produced and no proposal was submitted. Fix: `_generate_letter` returns
    a provenance marker; `execute()` returns a clear FAILURE when no letter
    could be produced and never reads as "submitted/applied/done" when it
    only drafted (this skill never submits — submission is a separate step).

All collaborators (registry, eco_router, gig store, profile) are mocked.
No network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.skills.builtin.survival.apply_skill import (
    ApplyJobSkill,
    _LETTER_SOURCE_FAILED,
    _LETTER_SOURCE_LLM,
    _LETTER_SOURCE_TEMPLATE,
)
from lazyclaw.survival.profile import SkillsProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB = {
    "title": "Build a Google Maps scraper",
    "description": "Scrape business listings from Google Maps into a sheet.",
    "budget": "$200",
    "url": "https://www.upwork.com/jobs/~0gmaps",
    "platform": "upwork",
}


def _profile(branding_mode: str = "lazyclaw") -> SkillsProfile:
    return SkillsProfile(
        skills=("python", "scraping", "automation"),
        title="Python Developer",
        bio="AI-assisted automation dev.",
        branding_mode=branding_mode,
        display_name="Vato",
        min_hourly_rate=25.0,
    )


def _registry_with_dead_claude_mcp() -> MagicMock:
    """Registry that exposes a Claude Code MCP tool which would BLOW UP.

    The whole point of Fix A is that this tool must NEVER be invoked for
    letter generation. We wire its `execute` to raise so any regression
    that reintroduces the MCP-first path fails loudly.
    """
    dead_mcp = MagicMock()
    dead_mcp.execute = AsyncMock(
        side_effect=RuntimeError("Credit balance is too low"),
    )
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[
        {"function": {"name": "mcp_abc_claude_code_run"}},
    ])
    registry.get = MagicMock(return_value=dead_mcp)
    return registry, dead_mcp


def _patch_eco_router(chat_return):
    """Patch EcoRouter at its source so `.chat` is fully mocked.

    `_generate_letter` does `from lazyclaw.llm.eco_router import EcoRouter`
    locally, so patching the source attribute is what intercepts it.
    `chat_return` may be a response object or an Exception to raise.
    """
    eco = MagicMock()
    if isinstance(chat_return, Exception):
        eco.chat = AsyncMock(side_effect=chat_return)
    else:
        eco.chat = AsyncMock(return_value=chat_return)
    return patch("lazyclaw.llm.eco_router.EcoRouter", return_value=eco), eco


def _llm_resp(text: str):
    resp = MagicMock()
    resp.content = text
    return resp


# ---------------------------------------------------------------------------
# Fix A — SDK/EcoRouter is the PRIMARY letter generator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eco_router_is_primary_even_when_claude_mcp_present():
    """EcoRouter produces the letter; the dead Claude Code MCP is never called."""
    registry, dead_mcp = _registry_with_dead_claude_mcp()
    skill = ApplyJobSkill(config=None, registry=registry)

    eco_patch, eco = _patch_eco_router(_llm_resp("SDK-written tailored letter."))
    with eco_patch:
        letter, source = await skill._generate_letter(
            "user-1", _JOB, _profile(), custom_note="",
        )

    assert source == _LETTER_SOURCE_LLM
    assert letter == "SDK-written tailored letter."
    # The Claude Code MCP letter-gen step must be GONE — never invoked.
    assert dead_mcp.execute.await_count == 0
    eco.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_letter_gen_succeeds_via_eco_router_when_mcp_unavailable():
    """No MCP tools at all → EcoRouter still produces the letter (SDK path)."""
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    registry.get = MagicMock(return_value=None)
    skill = ApplyJobSkill(config=None, registry=registry)

    eco_patch, eco = _patch_eco_router(_llm_resp("Letter via subscription SDK."))
    with eco_patch:
        letter, source = await skill._generate_letter(
            "user-1", _JOB, _profile(), custom_note="",
        )

    assert source == _LETTER_SOURCE_LLM
    assert letter == "Letter via subscription SDK."
    eco.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_claude_cli_or_mcp_used_for_letter_gen():
    """Guard: the source no longer references the Claude Code MCP / claude -p ladder."""
    import inspect

    from lazyclaw.skills.builtin.survival import apply_skill

    src = inspect.getsource(apply_skill.ApplyJobSkill._generate_letter)
    # The letter-gen function must not invoke a claude-code MCP tool.
    assert "claude" not in src.lower() or "claude-agent-sdk" in src.lower() \
        or "ANTHROPIC_API_KEY" in src, (
        "letter-gen ladder should not invoke Claude Code MCP / claude -p"
    )
    # It must go through EcoRouter.
    assert "EcoRouter" in src


# ---------------------------------------------------------------------------
# Template deep-fallback still works as last resort
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_template_fallback_when_eco_router_fails():
    """EcoRouter raising → deterministic template, flagged as TEMPLATE source."""
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    skill = ApplyJobSkill(config=None, registry=registry)

    eco_patch, _eco = _patch_eco_router(RuntimeError("subscription down"))
    with eco_patch:
        letter, source = await skill._generate_letter(
            "user-1", _JOB, _profile(branding_mode="lazyclaw"), custom_note="",
        )

    assert source == _LETTER_SOURCE_TEMPLATE
    assert "LazyClaw" in letter
    assert letter.strip()


@pytest.mark.asyncio
async def test_template_fallback_when_eco_router_returns_empty():
    """EcoRouter returning blank text → template, not an empty 'success'."""
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    skill = ApplyJobSkill(config=None, registry=registry)

    eco_patch, _eco = _patch_eco_router(_llm_resp("   "))
    with eco_patch:
        letter, source = await skill._generate_letter(
            "user-1", _JOB, _profile(branding_mode="personal"), custom_note="",
        )

    assert source == _LETTER_SOURCE_TEMPLATE
    assert letter.strip()
    assert "LazyClaw" not in letter  # personal branding


# ---------------------------------------------------------------------------
# Fix B — truthful FAILURE when nothing can be produced
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_letter_returns_failed_when_all_paths_fail():
    """EcoRouter fails AND template raises → FAILED marker, empty text."""
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    skill = ApplyJobSkill(config=None, registry=registry)

    eco_patch, _eco = _patch_eco_router(RuntimeError("subscription down"))
    # Force the template path to blow up too.
    with eco_patch, patch.object(
        ApplyJobSkill, "_template_letter",
        side_effect=RuntimeError("template boom"),
    ):
        letter, source = await skill._generate_letter(
            "user-1", _JOB, _profile(), custom_note="",
        )

    assert source == _LETTER_SOURCE_FAILED
    assert letter == ""


def _patch_execute_collaborators():
    """Patch the DB/profile helpers `execute()` imports locally."""
    return [
        patch(
            "lazyclaw.survival.profile.get_profile",
            new=AsyncMock(return_value=_profile()),
        ),
        patch(
            "lazyclaw.survival.gig.list_gigs",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "lazyclaw.survival.gig.update_gig_status",
            new=AsyncMock(return_value=None),
        ),
    ]


@pytest.mark.asyncio
async def test_execute_reports_failure_when_letter_gen_fails():
    """When letter gen totally fails, execute() returns FAILURE, not 'done'."""
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    registry.get = MagicMock(return_value=None)
    skill = ApplyJobSkill(config=None, registry=registry)

    patches = _patch_execute_collaborators()
    for p in patches:
        p.start()
    try:
        # Resolve the job from the passed URL via the MCP bridge — but here
        # we short-circuit resolution by patching _generate_letter to FAILED
        # and resolution to return our job directly.
        with patch.object(
            ApplyJobSkill, "_resolve_from_memory",
            new=AsyncMock(return_value=_JOB),
        ), patch.object(
            ApplyJobSkill, "_generate_letter",
            new=AsyncMock(return_value=("", _LETTER_SOURCE_FAILED)),
        ):
            result = await skill.execute(
                "user-1", {"job_reference": _JOB["url"]},
            )
    finally:
        for p in patches:
            p.stop()

    lower = result.lower()
    assert "fail" in lower
    assert "no proposal was submitted" in lower
    # Must NOT read as a completed application.
    assert "proposal ready" not in lower
    assert "proposal form filled" not in lower


@pytest.mark.asyncio
async def test_execute_draft_does_not_read_as_submitted():
    """A successful LLM draft must clearly say it's NOT submitted yet."""
    # Non-browser platform path is simplest to assert wording on.
    job = dict(_JOB, platform="freelancer")  # not in BROWSER_PLATFORMS
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    registry.get = MagicMock(return_value=None)
    skill = ApplyJobSkill(config=None, registry=registry)

    patches = _patch_execute_collaborators()
    for p in patches:
        p.start()
    try:
        with patch.object(
            ApplyJobSkill, "_resolve_from_memory",
            new=AsyncMock(return_value=job),
        ), patch.object(
            ApplyJobSkill, "_generate_letter",
            new=AsyncMock(return_value=("A tailored letter.", _LETTER_SOURCE_LLM)),
        ):
            result = await skill.execute(
                "user-1", {"job_reference": job["url"]},
            )
    finally:
        for p in patches:
            p.stop()

    lower = result.lower()
    assert "A tailored letter." in result
    # Truthful: draft only, not submitted.
    assert "draft" in lower
    assert "not yet submitted" in lower or "nothing has been sent yet" in lower
    # Degraded note must be ABSENT for an LLM-sourced letter.
    assert "template draft" not in lower


@pytest.mark.asyncio
async def test_execute_flags_template_draft_as_degraded():
    """Template-sourced draft is flagged degraded so it isn't passed off as model-written."""
    job = dict(_JOB, platform="freelancer")
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[])
    registry.get = MagicMock(return_value=None)
    skill = ApplyJobSkill(config=None, registry=registry)

    patches = _patch_execute_collaborators()
    for p in patches:
        p.start()
    try:
        with patch.object(
            ApplyJobSkill, "_resolve_from_memory",
            new=AsyncMock(return_value=job),
        ), patch.object(
            ApplyJobSkill, "_generate_letter",
            new=AsyncMock(return_value=("Generic template letter.", _LETTER_SOURCE_TEMPLATE)),
        ):
            result = await skill.execute(
                "user-1", {"job_reference": job["url"]},
            )
    finally:
        for p in patches:
            p.stop()

    lower = result.lower()
    assert "template" in lower
    assert "draft" in lower
