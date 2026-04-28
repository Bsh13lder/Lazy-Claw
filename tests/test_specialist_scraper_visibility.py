"""Browser/Research specialists must see mcp-scraper tools at runtime.

Without this, `delegate(specialist="browser"|"research")` flows fall back
to opening Chrome for read-only contact-data work that `extract_entities`
would solve in one JS-rendered call. Scraper tool names are dynamic
(`mcp_<server_uuid>_<toolname>`) so they must be unioned in at runtime
via the `include_scraper` flag, not hard-coded in `allowed_skills`.

Also pins the dispatch_subagents skill caps (≥2, ≤5) so the brain can't
spawn the 21-subagent fan-outs we saw in production logs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lazyclaw.teams.runner import _filter_tools
from lazyclaw.teams.specialist import (
    BROWSER_SPECIALIST,
    CODE_SPECIALIST,
    RESEARCH_SPECIALIST,
)


def _registry_with_scraper() -> MagicMock:
    reg = MagicMock()
    reg.list_tools.return_value = [
        {
            "function": {
                "name": "browser",
                "description": "Browser automation",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "function": {
                "name": "web_search",
                "description": "Web search",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "function": {
                "name": "read_file",
                "description": "Read a local file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    reg.list_mcp_tools.return_value = [
        {
            "function": {
                "name": "mcp_abc123_extract_entities",
                "description": (
                    "[MCP: mcp-scraper] JS-rendered entity extraction."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "function": {
                "name": "mcp_abc123_batch_crawl",
                "description": "[MCP: mcp-scraper] Batch crawl URLs.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "function": {
                "name": "mcp_other_send_email",
                "description": "Send email via mcp-email.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    return reg


def test_browser_specialist_opts_into_scraper():
    assert BROWSER_SPECIALIST.include_scraper is True


def test_research_specialist_opts_into_scraper():
    assert RESEARCH_SPECIALIST.include_scraper is True


def test_code_specialist_does_not_get_scraper():
    """Code work doesn't need scraper — keep the prompt focused."""
    assert CODE_SPECIALIST.include_scraper is False


def test_filter_tools_unions_scraper_when_opted_in():
    reg = _registry_with_scraper()
    out = _filter_tools(reg, ("browser", "web_search"), include_scraper=True)
    names = {t["function"]["name"] for t in out}
    assert "browser" in names
    assert "web_search" in names
    assert "mcp_abc123_extract_entities" in names
    assert "mcp_abc123_batch_crawl" in names
    # Unrelated MCP tool stays out
    assert "mcp_other_send_email" not in names


def test_filter_tools_omits_scraper_when_not_opted_in():
    reg = _registry_with_scraper()
    out = _filter_tools(reg, ("browser", "web_search"), include_scraper=False)
    names = {t["function"]["name"] for t in out}
    assert "mcp_abc123_extract_entities" not in names
    assert "mcp_abc123_batch_crawl" not in names


def test_filter_tools_handles_missing_mcp_registry():
    """If list_mcp_tools blows up, the base allowed set still works."""
    reg = MagicMock()
    reg.list_tools.return_value = [
        {
            "function": {
                "name": "browser",
                "description": "Browser",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    reg.list_mcp_tools.side_effect = RuntimeError("registry not ready")
    out = _filter_tools(reg, ("browser",), include_scraper=True)
    assert [t["function"]["name"] for t in out] == ["browser"]


# ── dispatch_subagents skill caps ─────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_skill_rejects_single_task_with_run_background_hint():
    """1 task → error must point at run_background, not delegate (blocking)."""
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill

    skill = DispatchSubagentsSkill(
        config=MagicMock(),
        registry=MagicMock(),
        eco_router=MagicMock(),
        permission_checker=None,
        team_lead=MagicMock(),
    )
    out = await skill.execute("u1", {
        "tasks": [{"type": "explore", "task": "find one email"}],
    })
    assert "run_background" in out
    assert "Error" in out


@pytest.mark.asyncio
async def test_dispatch_skill_caps_at_five():
    """6+ tasks → error must point at batch scraper tools, not 21 subagents."""
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill

    skill = DispatchSubagentsSkill(
        config=MagicMock(),
        registry=MagicMock(),
        eco_router=MagicMock(),
        permission_checker=None,
        team_lead=MagicMock(),
    )
    out = await skill.execute("u1", {
        "tasks": [
            {"type": "explore", "task": f"find email for biz {i}"}
            for i in range(8)
        ],
    })
    assert "Error" in out
    assert "5" in out  # mentions the cap
    assert "run_background" in out
    assert "batch" in out.lower()


@pytest.mark.asyncio
async def test_dispatch_skill_accepts_same_shape_chunks_under_cap():
    """3 same-shape tasks — chunked fan-out — must SUCCEED.

    The architecture is: brain splits 21 items across 2-5 workers, each
    worker batches its chunk internally. That gives every worker the
    same opening verb phrase. Earlier we rejected this by mistake; now
    the 5-cap is the safeguard, the foreground worker-loop drift is
    handled by ``detect_inline_pivot`` separately, and chunked
    dispatches pass through.
    """
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def fast_run_specialist(**kwargs):
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=1,
            success=True,
            error=None,
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(teams_runner, "run_specialist", fast_run_specialist)
    try:
        skill = DispatchSubagentsSkill(
            config=MagicMock(),
            registry=MagicMock(),
            eco_router=MagicMock(),
            permission_checker=None,
            team_lead=MagicMock(),
        )
        out = await skill.execute("u1", {
            "tasks": [
                {"type": "explore", "task": "Find contact emails for chunk 1: Salons A-G"},
                {"type": "explore", "task": "Find contact emails for chunk 2: Salons H-N"},
                {"type": "explore", "task": "Find contact emails for chunk 3: Salons O-U"},
            ],
        })
        assert out.startswith("Dispatched 3 subagents")
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_dispatch_skill_accepts_different_shape_tasks():
    """3 tasks with DIFFERENT goals must still go through. Pins the
    architectural rule that dispatch_subagents IS for 2-5 different
    goals, not just for batch fan-out.
    """
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def fast_run_specialist(**kwargs):
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=1,
            success=True,
            error=None,
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(teams_runner, "run_specialist", fast_run_specialist)
    try:
        skill = DispatchSubagentsSkill(
            config=MagicMock(),
            registry=MagicMock(),
            eco_router=MagicMock(),
            permission_checker=None,
            team_lead=MagicMock(),
        )
        out = await skill.execute("u1", {
            "tasks": [
                {"type": "explore", "task": "Research Apple's latest earnings"},
                {"type": "explore", "task": "Summarize the changelog of Vite 6"},
                {"type": "explore", "task": "Find papers on diffusion model distillation"},
            ],
        })
        assert out.startswith("Dispatched 3 subagents")
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_dispatch_skill_accepts_five():
    """Exactly 5 tasks must succeed — the cap is inclusive."""
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def fast_run_specialist(**kwargs):
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=1,
            success=True,
            error=None,
        )

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(teams_runner, "run_specialist", fast_run_specialist)
    try:
        skill = DispatchSubagentsSkill(
            config=MagicMock(),
            registry=MagicMock(),
            eco_router=MagicMock(),
            permission_checker=None,
            team_lead=MagicMock(),
        )
        out = await skill.execute("u1", {
            "tasks": [
                {"type": "explore", "task": f"different goal {i}"}
                for i in range(5)
            ],
        })
        assert out.startswith("Dispatched 5 subagents")
    finally:
        monkeypatch.undo()
