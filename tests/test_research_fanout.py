"""Tests for the research-first fan-out (ADR-0005, Phase 4).

Two surfaces are locked here:
  1. The two new research-specialist ``.md`` files parse into valid
     ``SpecialistConfig`` objects with the exact frontmatter Phase 4 requires.
  2. ``gather_specialist_research`` orchestrates ``run_specialist`` correctly —
     it runs both lanes in parallel, tolerates one lane failing, and collapses
     to ``''`` when nothing useful comes back.

NO real network / LLM calls: ``lazyclaw.teams.runner.run_specialist`` is
monkeypatched with a fake that returns canned ``SpecialistResult`` objects.
"""

from __future__ import annotations

import asyncio

import pytest

from lazyclaw.runtime import research_fanout
from lazyclaw.runtime.research_fanout import gather_specialist_research
from lazyclaw.teams.runner import SpecialistResult
from lazyclaw.teams.specialist import SpecialistConfig
from lazyclaw.teams.specialist_loader import (
    BUILTIN_SPECIALISTS_DIR,
    parse_specialist_md,
)


# ── .md definitions parse into valid SpecialistConfig ──────────────────


def _parse(filename: str) -> SpecialistConfig:
    text = (BUILTIN_SPECIALISTS_DIR / filename).read_text(encoding="utf-8")
    return parse_specialist_md(text, is_builtin=True)


def test_code_research_specialist_md_parses():
    cfg = _parse("code_research_specialist.md")
    assert isinstance(cfg, SpecialistConfig)
    assert cfg.name == "code_research_specialist"
    assert cfg.display_name == "Code Research Specialist"
    assert cfg.preferred_model is None          # no model line → defaults None
    assert cfg.include_scraper is False
    assert cfg.allowed_skills == (
        "read_file",
        "list_directory",
        "run_command",
        "search_tools",
    )
    assert cfg.is_builtin is True
    # Read-only contract is spelled out in the prompt.
    assert "READ-ONLY" in cfg.system_prompt
    assert "file:line" in cfg.system_prompt or "file.py:line" in cfg.system_prompt


def test_web_research_specialist_md_parses():
    cfg = _parse("web_research_specialist.md")
    assert isinstance(cfg, SpecialistConfig)
    assert cfg.name == "web_research_specialist"
    assert cfg.display_name == "Web Research Specialist"
    assert cfg.preferred_model is None          # no model line → defaults None
    assert cfg.include_scraper is True
    assert cfg.allowed_skills == ("web_search", "search_tools", "browser")
    assert cfg.is_builtin is True
    assert "URL" in cfg.system_prompt
    assert "from memory" in cfg.system_prompt


# ── Fake run_specialist plumbing ───────────────────────────────────────


class _Tracker:
    """Records concurrency + call order for the fake ``run_specialist``."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []


def _result(name: str, text: str, *, success: bool = True) -> SpecialistResult:
    """Build a canned SpecialistResult the way the real runner would."""
    return SpecialistResult(
        agent_name=name,
        task="<task>",
        result=text,
        tools_used=(),
        model_used="fake",
        duration_ms=1,
        success=success,
    )


def _install_fake(monkeypatch, tracker: _Tracker, outcomes: dict[str, object]):
    """Patch ``lazyclaw.teams.runner.run_specialist`` with a tracking fake.

    ``outcomes`` maps specialist.name → either a SpecialistResult to return or
    an Exception instance to raise. Each call records concurrency so tests can
    assert the lanes actually overlapped.
    """

    async def fake_run_specialist(*, specialist, task, **kwargs):
        tracker.active += 1
        tracker.max_active = max(tracker.max_active, tracker.active)
        tracker.calls.append(specialist.name)
        try:
            await asyncio.sleep(0.02)  # force overlap window for parallelism
            outcome = outcomes[specialist.name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            tracker.active -= 1

    monkeypatch.setattr(
        "lazyclaw.teams.runner.run_specialist", fake_run_specialist
    )


_KW = dict(
    registry=object(),
    eco_router=object(),
    permission_checker=object(),
)


# ── gather_specialist_research — happy path, both lanes in parallel ────


async def test_both_lanes_run_in_parallel(monkeypatch):
    tracker = _Tracker()
    _install_fake(
        monkeypatch,
        tracker,
        {
            "code_research_specialist": _result(
                "code_research_specialist", "found it at foo.py:42"
            ),
            "web_research_specialist": _result(
                "web_research_specialist", "docs say X (https://x.dev)"
            ),
        },
    )

    out = await gather_specialist_research(
        object(), "u1", "how does the widget work?", **_KW
    )

    # Both specialists were dispatched, and they overlapped (true parallelism).
    assert set(tracker.calls) == {
        "code_research_specialist",
        "web_research_specialist",
    }
    assert tracker.max_active == 2

    # Synthesized block carries both sections with their findings.
    assert out.startswith("## Research findings")
    assert "### Code" in out and "found it at foo.py:42" in out
    assert "### Web" in out and "docs say X (https://x.dev)" in out


async def test_partial_failure_one_lane_still_returns(monkeypatch):
    tracker = _Tracker()
    _install_fake(
        monkeypatch,
        tracker,
        {
            # Code lane blows up...
            "code_research_specialist": RuntimeError("grep exploded"),
            # ...web lane still succeeds.
            "web_research_specialist": _result(
                "web_research_specialist", "web answer here"
            ),
        },
    )

    out = await gather_specialist_research(
        object(), "u1", "research this", **_KW
    )

    # Both were attempted in parallel; only the surviving lane is in the block.
    assert tracker.max_active == 2
    assert out.startswith("## Research findings")
    assert "### Web" in out and "web answer here" in out
    assert "### Code" not in out


async def test_empty_results_collapse_to_empty_string(monkeypatch):
    tracker = _Tracker()
    _install_fake(
        monkeypatch,
        tracker,
        {
            "code_research_specialist": _result("code_research_specialist", "   "),
            "web_research_specialist": _result("web_research_specialist", ""),
        },
    )

    out = await gather_specialist_research(object(), "u1", "anything", **_KW)
    assert out == ""


async def test_both_lanes_fail_returns_empty(monkeypatch):
    tracker = _Tracker()
    _install_fake(
        monkeypatch,
        tracker,
        {
            "code_research_specialist": RuntimeError("boom"),
            "web_research_specialist": RuntimeError("bang"),
        },
    )
    out = await gather_specialist_research(object(), "u1", "anything", **_KW)
    assert out == ""


async def test_unsuccessful_result_is_dropped(monkeypatch):
    """A SpecialistResult with success=False must not enter the block."""
    tracker = _Tracker()
    _install_fake(
        monkeypatch,
        tracker,
        {
            "code_research_specialist": _result(
                "code_research_specialist", "stuck details", success=False
            ),
            "web_research_specialist": _result(
                "web_research_specialist", "good web data"
            ),
        },
    )
    out = await gather_specialist_research(object(), "u1", "x", **_KW)
    assert "### Code" not in out
    assert "### Web" in out and "good web data" in out


async def test_want_flags_select_lanes(monkeypatch):
    tracker = _Tracker()
    _install_fake(
        monkeypatch,
        tracker,
        {
            "code_research_specialist": _result(
                "code_research_specialist", "code only"
            ),
            "web_research_specialist": _result(
                "web_research_specialist", "should not run"
            ),
        },
    )

    out = await gather_specialist_research(
        object(), "u1", "repo question", want_web=False, **_KW
    )

    assert tracker.calls == ["code_research_specialist"]
    assert "### Code" in out and "code only" in out
    assert "### Web" not in out


async def test_blank_message_returns_empty_without_dispatch(monkeypatch):
    tracker = _Tracker()
    _install_fake(monkeypatch, tracker, {})  # nothing should be called
    out = await gather_specialist_research(object(), "u1", "   ", **_KW)
    assert out == ""
    assert tracker.calls == []


async def test_no_lanes_requested_returns_empty(monkeypatch):
    tracker = _Tracker()
    _install_fake(monkeypatch, tracker, {})
    out = await gather_specialist_research(
        object(), "u1", "real message", want_code=False, want_web=False, **_KW
    )
    assert out == ""
    assert tracker.calls == []


async def test_lane_timeout_is_tolerated(monkeypatch):
    """A lane that exceeds its timeout is dropped, not raised."""
    tracker = _Tracker()

    async def slow_run_specialist(*, specialist, task, **kwargs):
        tracker.calls.append(specialist.name)
        if specialist.name == "code_research_specialist":
            await asyncio.sleep(5)  # will exceed the patched 0.05s timeout
            return _result(specialist.name, "too late")
        return _result(specialist.name, "fast web answer")

    monkeypatch.setattr(
        "lazyclaw.teams.runner.run_specialist", slow_run_specialist
    )
    monkeypatch.setenv("LAZYCLAW_RESEARCH_LANE_TIMEOUT", "0.05")

    out = await gather_specialist_research(object(), "u1", "q", **_KW)

    assert "### Code" not in out          # timed-out lane dropped
    assert "### Web" in out and "fast web answer" in out


# ── lane-output clipping ───────────────────────────────────────────────


async def test_long_lane_output_is_clipped(monkeypatch):
    tracker = _Tracker()
    huge = "x" * (research_fanout._MAX_LANE_CHARS + 500)
    _install_fake(
        monkeypatch,
        tracker,
        {
            "code_research_specialist": _result("code_research_specialist", huge),
            "web_research_specialist": _result("web_research_specialist", "short"),
        },
    )
    out = await gather_specialist_research(object(), "u1", "q", **_KW)
    assert "… [truncated]" in out
    # Block stays bounded: clip cap + the short web section + headers/markers.
    assert len(out) < research_fanout._MAX_LANE_CHARS + 200
