"""Unit tests for profile-matched Upwork search as default.

Covers the design shipped 2026-05-13:

- `SkillsProfile.upwork_search_source` defaults to "best_matches"
- `SearchJobsSkill._try_upwork` passes `source` to the MCP, with explicit
  user keywords always overriding the profile preference (`source="search"`).
- `SetSkillsProfileSkill` schema enum exposes both modes for the LLM.
- Old user settings (missing the field) round-trip safely with the dataclass
  default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.skills.builtin.survival.profile_skill import SetSkillsProfileSkill
from lazyclaw.skills.builtin.survival.search_skill import SearchJobsSkill
from lazyclaw.survival.profile import DEFAULT_PROFILE, _profile_from_data


# ---------------------------------------------------------------------------
# SkillsProfile field
# ---------------------------------------------------------------------------

def test_default_profile_field_is_best_matches():
    """Brand-new users default to profile-matched Upwork searches."""
    assert DEFAULT_PROFILE.upwork_search_source == "best_matches"


def test_dataclass_default_applies_for_missing_field():
    """Old saved profiles (no field) get the dataclass default."""
    # Profile saved before this feature shipped — no upwork_search_source key.
    legacy_data = {
        "skills": ["python"],
        "title": "Python Dev",
        "min_hourly_rate": 30.0,
        "platforms": ["upwork"],
    }
    profile = _profile_from_data(legacy_data)
    assert profile.upwork_search_source == "best_matches"


def test_profile_from_data_round_trips_explicit_search_mode():
    """Saved 'search' preference survives load."""
    data = {"upwork_search_source": "search"}
    profile = _profile_from_data(data)
    assert profile.upwork_search_source == "search"


# ---------------------------------------------------------------------------
# Profile coerce / merge round-trip (in-memory, no sqlite)
# ---------------------------------------------------------------------------

def test_coerce_updates_accepts_upwork_search_source():
    """`_coerce_updates` must keep the new string field; the catch-all
    `else` branch is the same path branding_mode and work_hours use."""
    from lazyclaw.survival.profile import _coerce_updates

    result = _coerce_updates({"upwork_search_source": "search"})
    assert isinstance(result, dict)
    assert result["upwork_search_source"] == "search"


def test_coerce_updates_drops_unknown_keys():
    """Sanity: unknown keys are still ignored (regression guard for the
    field-allowlist mechanism we're piggy-backing on)."""
    from lazyclaw.survival.profile import _coerce_updates

    result = _coerce_updates({
        "upwork_search_source": "best_matches",
        "totally_not_a_real_field": "x",
    })
    assert isinstance(result, dict)
    assert "totally_not_a_real_field" not in result
    assert result["upwork_search_source"] == "best_matches"


# ---------------------------------------------------------------------------
# profile_skill schema
# ---------------------------------------------------------------------------

def test_profile_skill_schema_advertises_enum():
    """LLM-visible schema must list both legal values."""
    schema = SetSkillsProfileSkill(config=None).parameters_schema
    field = schema["properties"]["upwork_search_source"]
    assert field["type"] == "string"
    assert field["enum"] == ["best_matches", "search"]


def test_profile_skill_description_mentions_nl_phrase():
    desc = SetSkillsProfileSkill(config=None).description.lower()
    assert "match my upwork profile only" in desc
    assert "set upwork search to keyword" in desc


# ---------------------------------------------------------------------------
# SearchJobsSkill._try_upwork — source resolution
# ---------------------------------------------------------------------------

def _build_search_skill_with_mock_mcp(search_return=None):
    """Wire SearchJobsSkill against a fake registry exposing upwork MCP."""
    upwork_tool = MagicMock()
    upwork_tool.name = "mcp_xyz_upwork_search_jobs"
    upwork_tool.execute = AsyncMock(return_value=search_return or [])

    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[
        {"function": {"name": "mcp_xyz_upwork_search_jobs"}},
    ])
    registry.get = MagicMock(return_value=upwork_tool)

    skill = SearchJobsSkill(config=None, registry=registry)
    return skill, upwork_tool


@pytest.mark.asyncio
async def test_try_upwork_no_explicit_keywords_uses_best_matches():
    """Empty keywords → source=best_matches, query omitted."""
    skill, tool = _build_search_skill_with_mock_mcp()
    await skill._try_upwork(
        "user-1", "python fastapi", max_results=20,
        keywords_explicit=False,
        profile_source="best_matches",
    )
    tool.execute.assert_awaited_once()
    args = tool.execute.await_args.args[1]
    assert args["source"] == "best_matches"
    assert "query" not in args
    assert args["limit"] == 20


@pytest.mark.asyncio
async def test_try_upwork_explicit_keywords_force_search_source():
    """Explicit keywords always win over profile → source=search."""
    skill, tool = _build_search_skill_with_mock_mcp()
    await skill._try_upwork(
        "user-1", "python scraping", max_results=10,
        keywords_explicit=True,
        profile_source="best_matches",  # profile says best_matches, ignored
    )
    args = tool.execute.await_args.args[1]
    assert args["source"] == "search"
    assert args["query"] == "python scraping"


@pytest.mark.asyncio
async def test_try_upwork_profile_search_override_no_explicit_keywords():
    """Profile pinned to 'search' → use skills-joined keyword + source=search."""
    skill, tool = _build_search_skill_with_mock_mcp()
    await skill._try_upwork(
        "user-1", "python fastapi react", max_results=10,
        keywords_explicit=False,
        profile_source="search",
    )
    args = tool.execute.await_args.args[1]
    assert args["source"] == "search"
    assert args["query"] == "python fastapi react"


@pytest.mark.asyncio
async def test_try_upwork_handles_none_profile_source():
    """A None profile_source defensively falls back to best_matches."""
    skill, tool = _build_search_skill_with_mock_mcp()
    await skill._try_upwork(
        "user-1", "x", max_results=5,
        keywords_explicit=False,
        profile_source=None,
    )
    args = tool.execute.await_args.args[1]
    assert args["source"] == "best_matches"
    assert "query" not in args


@pytest.mark.asyncio
async def test_try_upwork_clamps_limit():
    skill, tool = _build_search_skill_with_mock_mcp()
    await skill._try_upwork(
        "user-1", "x", max_results=999,  # would exceed MCP max
        keywords_explicit=True, profile_source="search",
    )
    args = tool.execute.await_args.args[1]
    assert args["limit"] == 50


@pytest.mark.asyncio
async def test_try_upwork_returns_empty_when_no_registry():
    skill = SearchJobsSkill(config=None, registry=None)
    assert await skill._try_upwork(
        "user-1", "x", max_results=10,
        keywords_explicit=False, profile_source="best_matches",
    ) == []


@pytest.mark.asyncio
async def test_try_upwork_swallows_mcp_exception():
    skill, tool = _build_search_skill_with_mock_mcp()
    tool.execute = AsyncMock(side_effect=RuntimeError("net down"))
    result = await skill._try_upwork(
        "user-1", "x", max_results=10,
        keywords_explicit=True, profile_source="search",
    )
    assert result == []
