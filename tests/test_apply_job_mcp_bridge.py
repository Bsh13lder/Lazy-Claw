"""Unit tests for apply_job's MCP fallback bridge.

Covers the bug fixed 2026-05-13: when the brain searches via the raw
`upwork_search_jobs` MCP tool (instead of the native `search_jobs` skill),
no `SURVIVAL_SEARCH:` memory is written. `apply_job` used to return
"No recent job search" and loop. The bridge now resolves the job
directly through mcp-upwork by URL or title.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.skills.builtin.survival.apply_skill import ApplyJobSkill


# ---- _normalize_mcp_job ---------------------------------------------------

def test_normalize_mcp_job_full_shape():
    """Upwork MCP shape → apply_job shape."""
    job = ApplyJobSkill._normalize_mcp_job({
        "title": "WhatsApp automation",
        "description": "UK/US leads + different numbers",
        "budget": "$10K+",
        "url": "https://www.upwork.com/jobs/~0123",
    })
    assert job["title"] == "WhatsApp automation"
    assert job["budget"] == "$10K+"
    assert job["url"] == "https://www.upwork.com/jobs/~0123"
    assert job["platform"] == "upwork"


def test_normalize_mcp_job_relative_url_promoted():
    """Bare /jobs/~id gets upgraded to a full upwork URL."""
    job = ApplyJobSkill._normalize_mcp_job({
        "title": "x",
        "url": "/jobs/~abc",
    })
    assert job["url"] == "https://www.upwork.com/jobs/~abc"


def test_normalize_mcp_job_hourly_rate_alias():
    """hourly_rate is treated as budget when budget missing."""
    job = ApplyJobSkill._normalize_mcp_job({
        "title": "x",
        "hourly_rate": "$20-$40/hr",
    })
    assert job["budget"] == "$20-$40/hr"


def test_normalize_mcp_job_defaults_to_untitled():
    job = ApplyJobSkill._normalize_mcp_job({})
    assert job["title"] == "Untitled"
    assert job["budget"] == "N/A"
    assert job["url"] == ""


# ---- _coerce_to_job_list --------------------------------------------------

def test_coerce_list_passthrough():
    src = [{"title": "a"}, {"title": "b"}]
    assert ApplyJobSkill._coerce_to_job_list(src) == src


def test_coerce_list_from_json_string():
    src = json.dumps([{"title": "a"}])
    assert ApplyJobSkill._coerce_to_job_list(src) == [{"title": "a"}]


def test_coerce_list_unwraps_jobs_key():
    src = {"jobs": [{"title": "a"}, {"title": "b"}]}
    assert ApplyJobSkill._coerce_to_job_list(src) == [
        {"title": "a"}, {"title": "b"},
    ]


def test_coerce_list_unwraps_results_key():
    src = {"results": [{"title": "a"}]}
    assert ApplyJobSkill._coerce_to_job_list(src) == [{"title": "a"}]


def test_coerce_list_filters_non_dicts():
    src = [{"title": "a"}, "garbage", 42, None]
    assert ApplyJobSkill._coerce_to_job_list(src) == [{"title": "a"}]


def test_coerce_list_handles_none_and_invalid():
    assert ApplyJobSkill._coerce_to_job_list(None) == []
    assert ApplyJobSkill._coerce_to_job_list("not json") == []
    assert ApplyJobSkill._coerce_to_job_list(42) == []


# ---- _coerce_to_job_dict --------------------------------------------------

def test_coerce_dict_fills_fallback_url():
    """get_job_details that omits url falls back to the queried URL."""
    job = ApplyJobSkill._coerce_to_job_dict(
        {"title": "WhatsApp"},
        fallback_url="https://www.upwork.com/jobs/~xyz",
    )
    assert job is not None
    assert job["url"] == "https://www.upwork.com/jobs/~xyz"
    assert job["title"] == "WhatsApp"


def test_coerce_dict_keeps_existing_url():
    """Don't overwrite a URL the MCP already returned."""
    job = ApplyJobSkill._coerce_to_job_dict(
        {"title": "x", "url": "https://www.upwork.com/jobs/~aaa"},
        fallback_url="https://www.upwork.com/jobs/~bbb",
    )
    assert job["url"] == "https://www.upwork.com/jobs/~aaa"


def test_coerce_dict_parses_json_string():
    payload = json.dumps({"title": "x", "url": "https://www.upwork.com/jobs/~aaa"})
    job = ApplyJobSkill._coerce_to_job_dict(payload)
    assert job["title"] == "x"


def test_coerce_dict_rejects_garbage():
    assert ApplyJobSkill._coerce_to_job_dict(None) is None
    assert ApplyJobSkill._coerce_to_job_dict("not json") is None
    assert ApplyJobSkill._coerce_to_job_dict(42) is None


# ---- _resolve_via_upwork_mcp ----------------------------------------------

def _build_skill_with_mcp(details_return=None, search_return=None):
    """Wire a skill against a fake registry exposing both upwork MCP tools."""
    details_tool = MagicMock()
    details_tool.execute = AsyncMock(return_value=details_return)
    search_tool = MagicMock()
    search_tool.execute = AsyncMock(return_value=search_return)

    tool_map = {
        "mcp_xyz_upwork_get_job_details": details_tool,
        "mcp_xyz_upwork_search_jobs": search_tool,
    }
    registry = MagicMock()
    registry.list_mcp_tools = MagicMock(return_value=[
        {"function": {"name": "mcp_xyz_upwork_get_job_details"}},
        {"function": {"name": "mcp_xyz_upwork_search_jobs"}},
    ])
    registry.get = MagicMock(side_effect=tool_map.get)

    skill = ApplyJobSkill(config=None, registry=registry)
    return skill, details_tool, search_tool


@pytest.mark.asyncio
async def test_resolve_url_via_get_job_details():
    """URL ref → upwork_get_job_details, not upwork_search_jobs."""
    skill, details_tool, search_tool = _build_skill_with_mcp(
        details_return={
            "title": "WhatsApp automation (UK/US leads)",
            "description": "leads sheet + region-aware first DM",
            "budget": "$10K+",
            "url": "https://www.upwork.com/jobs/~0aaa",
        },
    )
    job = await skill._resolve_via_upwork_mcp(
        "user-1", "https://www.upwork.com/jobs/~0aaa",
    )
    assert job is not None
    assert job["title"] == "WhatsApp automation (UK/US leads)"
    assert details_tool.execute.await_count == 1
    assert search_tool.execute.await_count == 0


@pytest.mark.asyncio
async def test_resolve_title_via_search_jobs_picks_substring():
    """Title ref → search, then pick the entry whose title contains it."""
    skill, _details, search_tool = _build_skill_with_mcp(
        search_return=[
            {"title": "Python scraper", "url": "/jobs/~001"},
            {"title": "WhatsApp automation UK/US leads", "url": "/jobs/~002"},
            {"title": "Telegram bot", "url": "/jobs/~003"},
        ],
    )
    job = await skill._resolve_via_upwork_mcp("user-1", "WhatsApp automation")
    assert job is not None
    assert job["title"] == "WhatsApp automation UK/US leads"
    # URL got promoted to absolute form
    assert job["url"] == "https://www.upwork.com/jobs/~002"
    assert search_tool.execute.await_count == 1


@pytest.mark.asyncio
async def test_resolve_title_no_match_falls_back_to_first_result():
    """Search returned results but no title-substring match → take first."""
    skill, _details, _search = _build_skill_with_mcp(
        search_return=[{"title": "Random thing", "url": "/jobs/~001"}],
    )
    job = await skill._resolve_via_upwork_mcp("user-1", "Nothing matches")
    assert job is not None
    assert job["title"] == "Random thing"


@pytest.mark.asyncio
async def test_resolve_empty_results_returns_none():
    skill, _d, _s = _build_skill_with_mcp(search_return=[])
    assert await skill._resolve_via_upwork_mcp("user-1", "anything") is None


@pytest.mark.asyncio
async def test_resolve_swallows_mcp_exception():
    """MCP raising → return None, never propagate (avoid hard loop crashes)."""
    skill, details_tool, _ = _build_skill_with_mcp()
    details_tool.execute = AsyncMock(side_effect=RuntimeError("boom"))
    job = await skill._resolve_via_upwork_mcp(
        "user-1", "https://www.upwork.com/jobs/~broken",
    )
    assert job is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_registry_missing():
    """Skill without a registry can't reach MCP — caller decides fallback."""
    skill = ApplyJobSkill(config=None, registry=None)
    assert await skill._resolve_via_upwork_mcp("u", "anything") is None
