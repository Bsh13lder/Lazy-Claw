"""Tests for the declarative specialist loader (ADR-0005, Phase 1).

Locks two things:
  1. The `.md` parser maps frontmatter → SpecialistConfig correctly and
     fails loud on malformed input.
  2. The 3 builtins, now loaded from `lazyclaw/teams/specialists/*.md`,
     are byte-for-byte equivalent to their old inline definitions — so the
     conversion introduced zero behavior change.
"""
from __future__ import annotations

import textwrap

import pytest

from lazyclaw.teams.specialist import (
    BROWSER_SPECIALIST,
    BUILTIN_SPECIALISTS,
    CODE_SPECIALIST,
    RESEARCH_SPECIALIST,
    SpecialistConfig,
    get_defaults,
)
from lazyclaw.teams.specialist_loader import (
    BUILTIN_SPECIALISTS_DIR,
    load_builtin_specialists,
    parse_specialist_md,
)


# ── parse_specialist_md — happy path ──────────────────────────────────


def test_parse_full_frontmatter():
    md = textwrap.dedent(
        """\
        ---
        name: my_specialist
        display_name: My Specialist
        model: smart
        include_scraper: true
        tools: [browser, web_search, search_tools]
        ---
        You are a helpful specialist.

        Do the thing.
        """
    )
    cfg = parse_specialist_md(md, is_builtin=True)
    assert isinstance(cfg, SpecialistConfig)
    assert cfg.name == "my_specialist"
    assert cfg.display_name == "My Specialist"
    assert cfg.preferred_model == "smart"
    assert cfg.include_scraper is True
    assert cfg.allowed_skills == ("browser", "web_search", "search_tools")
    assert cfg.is_builtin is True
    assert cfg.system_prompt.startswith("You are a helpful specialist.")


def test_parse_minimal_defaults():
    """Only name + body required; everything else defaults."""
    md = "---\nname: bare\n---\nDo work."
    cfg = parse_specialist_md(md)
    assert cfg.name == "bare"
    assert cfg.display_name == "bare"  # falls back to name
    assert cfg.preferred_model is None
    assert cfg.include_scraper is False
    assert cfg.allowed_skills == ()
    assert cfg.is_builtin is False  # default
    assert cfg.system_prompt == "Do work."


def test_parse_block_style_tools_list():
    md = textwrap.dedent(
        """\
        ---
        name: blocky
        tools:
          - alpha
          - beta
          - gamma
          - delta
        ---
        Body.
        """
    )
    cfg = parse_specialist_md(md)
    assert cfg.allowed_skills == ("alpha", "beta", "gamma", "delta")


def test_parse_scalar_tool_coerced_to_tuple():
    md = "---\nname: one\ntools: solo\n---\nBody."
    cfg = parse_specialist_md(md)
    assert cfg.allowed_skills == ("solo",)


# ── parse_specialist_md — malformed → ValueError ──────────────────────


def test_parse_no_frontmatter_raises():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_specialist_md("Just a body, no fences.")


def test_parse_missing_name_raises():
    with pytest.raises(ValueError, match="name"):
        parse_specialist_md("---\ndisplay_name: No Name\n---\nBody.")


def test_parse_empty_body_raises():
    with pytest.raises(ValueError, match="empty system-prompt body"):
        parse_specialist_md("---\nname: hollow\n---\n   \n")


# ── load_builtin_specialists — from a temp dir ────────────────────────


def test_load_from_directory_sorted(tmp_path):
    (tmp_path / "zeta.md").write_text("---\nname: zeta\n---\nZ body.")
    (tmp_path / "alpha.md").write_text("---\nname: alpha\n---\nA body.")
    specs = load_builtin_specialists(tmp_path)
    assert [s.name for s in specs] == ["alpha", "zeta"]  # filename sort
    assert all(s.is_builtin for s in specs)


def test_load_malformed_builtin_raises_with_filename(tmp_path):
    (tmp_path / "broken.md").write_text("no frontmatter here")
    with pytest.raises(ValueError, match="broken.md"):
        load_builtin_specialists(tmp_path)


def test_load_empty_dir_returns_empty(tmp_path):
    assert load_builtin_specialists(tmp_path) == []


# ── Builtins: conversion fidelity (no behavior change) ────────────────


# Core specialists other modules import by name / depend on existing.
_CORE_SPECIALISTS = frozenset(
    {"browser_specialist", "code_specialist", "research_specialist"}
)
# Full domain set after ADR-0005 Phase 2-6 (specialist-first taxonomy).
_EXPECTED_SPECIALISTS = frozenset(
    {
        "browser_specialist", "code_specialist", "research_specialist",
        "code_research_specialist", "web_research_specialist",
        "freelance_specialist", "email_specialist", "messaging_specialist",
        "notes_specialist", "tasks_specialist", "documents_specialist",
        "contacts_specialist", "automation_specialist", "bounty_specialist",
        "system_specialist",
    }
)


def test_builtins_match_md_files_in_filename_order():
    names = [s.name for s in BUILTIN_SPECIALISTS]
    files = sorted(p.stem for p in BUILTIN_SPECIALISTS_DIR.glob("*.md"))
    assert names == files  # loader is filename-sorted, 1:1 with the .md files
    assert get_defaults() == list(BUILTIN_SPECIALISTS)
    # The three named handles other modules import still resolve.
    assert BROWSER_SPECIALIST.name == "browser_specialist"
    assert CODE_SPECIALIST.name == "code_specialist"
    assert RESEARCH_SPECIALIST.name == "research_specialist"


def test_expected_specialist_set_present():
    names = {s.name for s in BUILTIN_SPECIALISTS}
    assert _CORE_SPECIALISTS <= names  # never lose the core three
    assert _EXPECTED_SPECIALISTS <= names  # full ADR-0005 taxonomy shipped


def test_every_builtin_has_authored_body_and_unique_name():
    names = [s.name for s in BUILTIN_SPECIALISTS]
    assert len(names) == len(set(names))  # no duplicate specialists
    for s in BUILTIN_SPECIALISTS:
        assert s.system_prompt.strip(), f"{s.name} has empty body"
        assert "PLACEHOLDER" not in s.system_prompt, f"{s.name} body not authored"
        assert s.allowed_skills, f"{s.name} has no tools"


def test_validate_specialist_tools_flags_unknown():
    from lazyclaw.teams.specialist_loader import validate_specialist_tools

    good = parse_specialist_md("---\nname: g\ntools: [known_a, known_b]\n---\nBody.")
    bad = parse_specialist_md("---\nname: b\ntools: [known_a, nope_tool]\n---\nBody.")
    scraper = parse_specialist_md(
        "---\nname: s\ntools: [known_a, mcp_abc_extract]\n---\nBody."
    )
    report = validate_specialist_tools(
        [good, bad, scraper], known_tool_names={"known_a", "known_b"}
    )
    assert report == {"b": ["nope_tool"]}  # mcp_* dynamic tools never flagged


def test_browser_specialist_fields():
    s = BROWSER_SPECIALIST
    assert s.display_name == "Browser Specialist"
    assert s.preferred_model == "smart"
    assert s.include_scraper is True
    assert s.is_builtin is True
    assert s.allowed_skills == (
        "browser",
        "use_host_browser",
        "web_search",
        "save_site_login",
        "payment",
        "search_tools",
        "google_run_task",
    )
    assert "PLAN-ACT-VALIDATE" in s.system_prompt


def test_code_specialist_fields():
    s = CODE_SPECIALIST
    assert s.display_name == "Code Specialist"
    assert s.preferred_model is None
    assert s.include_scraper is False
    assert s.allowed_skills == (
        "calculate",
        "create_skill",
        "list_skills",
        "delete_skill",
    )
    assert "EXECUTION LADDER" in s.system_prompt


def test_research_specialist_fields():
    s = RESEARCH_SPECIALIST
    assert s.display_name == "Research Specialist"
    assert s.preferred_model is None
    assert s.include_scraper is True
    assert s.allowed_skills == (
        "web_search",
        "browser",
        "read_file",
        "list_directory",
        "run_command",
        "search_tools",
        "google_run_task",
    )
    assert "TOOL LADDER" in s.system_prompt


def test_freelance_allowlists_raw_upwork_read_tools():
    """Regression (2026-06-10 00:33): the brain delegated "call
    upwork_get_conversation with room_id=..." but the freelance allowlist
    lacked the raw MCP read tools its own prompt ladder promises.
    search_tools discovery does NOT grant callability — _filter_tools only
    unions MCP tools whose bare name is allowlisted — so the worker
    stuck-looped on search_tools and the user never got an answer.
    """
    spec = next(
        s for s in BUILTIN_SPECIALISTS if s.name == "freelance_specialist"
    )
    assert {
        "upwork_get_messages",
        "upwork_get_conversation",
        "upwork_get_unread_count",
        "upwork_last_conversation",
        "upwork_inbox_check",
    } <= set(spec.allowed_skills)


def test_startup_self_check_accepts_bare_mcp_suffixes(caplog):
    """ADR-0005 startup self-check: MCP tool ids carry a mcp_<uuid>_
    prefix while allowlists hold bare names — both forms must count as
    known, and genuine typos must still warn."""
    import logging as _logging

    from lazyclaw.teams.specialist_loader import startup_specialist_self_check

    native = {s for spec in BUILTIN_SPECIALISTS for s in spec.allowed_skills}
    # Simulate a live registry: every allowlisted native name, plus the
    # upwork read tools exposed ONLY under their prefixed MCP ids.
    mcp_only = {
        "upwork_get_messages", "upwork_get_conversation",
        "upwork_get_unread_count", "upwork_last_conversation",
        "upwork_inbox_check", "upwork_submit_proposal",
        "upwork_contract_poll",
    }

    class _FakeRegistry:
        def list_tools(self):
            tools = [
                {"function": {"name": n}} for n in native - mcp_only
            ]
            tools += [
                {"function": {"name": f"mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_{n}"}}
                for n in mcp_only
            ]
            return tools

    with caplog.at_level(_logging.WARNING):
        report = startup_specialist_self_check(_FakeRegistry())
    assert report == {}, f"false positives: {report}"

    class _EmptyRegistry:
        def list_tools(self):
            return [{"function": {"name": "web_search"}}]

    with caplog.at_level(_logging.WARNING):
        report = startup_specialist_self_check(_EmptyRegistry())
    # With a near-empty registry the drift IS reported, loudly.
    assert "freelance_specialist" in report
    assert any("unknown tools" in r.message for r in caplog.records)
