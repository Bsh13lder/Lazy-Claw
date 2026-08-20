"""Shared alias resolver — one source of truth for delegate + agent."""
from lazyclaw.teams.specialist import SpecialistConfig
from lazyclaw.teams.specialist_aliases import (
    SPECIALIST_MAP,
    resolve_specialist,
    specialist_choices,
)


def test_short_alias_resolves_to_builtin():
    spec = resolve_specialist("browser")
    assert isinstance(spec, SpecialistConfig)
    assert spec.name == "browser_specialist"


def test_full_name_resolves():
    spec = resolve_specialist("freelance_specialist")
    assert spec is not None
    assert spec.name == "freelance_specialist"


def test_unknown_returns_none():
    assert resolve_specialist("nonexistent_agent_xyz") is None


def test_choices_cover_map_and_are_unique():
    choices = specialist_choices()
    assert len(choices) == len(set(choices))
    assert set(choices) == set(SPECIALIST_MAP.keys())
    assert "browser" in choices
    assert "upwork" in choices


def test_delegate_still_uses_same_map():
    # delegate.py must not keep a private fork of the map
    from lazyclaw.skills.builtin.delegate import _SPECIALIST_MAP
    assert _SPECIALIST_MAP is SPECIALIST_MAP


# ── 2026-08-19 MCP-restart incident: routing knowledge ────────────────


def test_mcp_alias_routes_to_automation():
    """'restart/install/connect an MCP server' must have an explicit
    alias to the specialist that owns the full MCP lifecycle."""
    spec = resolve_specialist("mcp")
    assert spec is not None
    assert spec.name == "automation_specialist"


def test_agent_type_roster_covers_every_builtin():
    """The compiled roster string must carry every builtin's description
    — this is the drift-proof replacement for the hand-written 3-of-17
    hint that rotted in delegate.py."""
    from lazyclaw.teams.specialist import BUILTIN_SPECIALISTS
    from lazyclaw.teams.specialist_aliases import AGENT_TYPE_ROSTER

    for spec in BUILTIN_SPECIALISTS:
        assert spec.description, (
            f"{spec.name} has no description: frontmatter — the roster "
            "needs one line per specialist"
        )
        assert spec.description in AGENT_TYPE_ROSTER, spec.name


def test_agent_type_roster_lines_keyed_by_enum_names():
    """Every roster line's primary key must be a valid agent_type enum
    choice, so the description teaches names the brain can actually use."""
    from lazyclaw.teams.specialist_aliases import AGENT_TYPE_ROSTER

    choices = set(specialist_choices())
    for line in AGENT_TYPE_ROSTER.splitlines():
        key = line.split(":", 1)[0].split(" ", 1)[0].strip()
        assert key in choices, line


def test_agent_type_roster_names_mcp_owner():
    """The incident's exact gap: nothing told the brain MCP lifecycle
    lives under `automation`."""
    from lazyclaw.teams.specialist_aliases import AGENT_TYPE_ROSTER

    auto_line = next(
        line for line in AGENT_TYPE_ROSTER.splitlines()
        if line.startswith("automation")
    )
    assert "MCP" in auto_line


def test_delegate_schema_carries_roster_too():
    """Legacy `delegate` must share the compiled roster, not keep its own
    hand-written (and drifted) copy."""
    from lazyclaw.skills.builtin.delegate import DelegateSkill
    from lazyclaw.teams.specialist_aliases import AGENT_TYPE_ROSTER

    skill = DelegateSkill(config=None, registry=None, eco_router=None)
    desc = skill.parameters_schema["properties"]["specialist"]["description"]
    assert AGENT_TYPE_ROSTER in desc
