"""Shared short-alias → builtin specialist resolution.

Extracted from skills/builtin/delegate.py so `delegate` (legacy) and
`agent` (unified dispatch, ADR spec 2026-07-07) resolve agent types
identically. New builtin `.md` specialists auto-register here.
"""
from __future__ import annotations

from lazyclaw.teams.specialist import BUILTIN_SPECIALISTS, SpecialistConfig

# Intent word → full builtin specialist name.
SHORT_ALIASES: dict[str, str] = {
    "browser": "browser_specialist",
    "research": "research_specialist",
    "code": "code_specialist",
    "code_research": "code_research_specialist",
    "web_research": "web_research_specialist",
    "freelance": "freelance_specialist",
    "upwork": "freelance_specialist",
    "gig": "freelance_specialist",
    "email": "email_specialist",
    "messaging": "messaging_specialist",
    "whatsapp": "messaging_specialist",
    "instagram": "messaging_specialist",
    "telegram": "messaging_specialist",
    "notes": "notes_specialist",
    "memory": "notes_specialist",
    "lazybrain": "notes_specialist",
    "tasks": "tasks_specialist",
    "budget": "tasks_specialist",
    "documents": "documents_specialist",
    "docs": "documents_specialist",
    "contacts": "contacts_specialist",
    "pipeline": "contacts_specialist",
    "automation": "automation_specialist",
    "n8n": "automation_specialist",
    # 2026-08-19 mcp-whatsapp restart incident: explicit MCP intent must
    # reach the full-lifecycle owner (system_specialist carries only the
    # restart trio: list/connect/disconnect).
    "mcp": "automation_specialist",
    "bounty": "bounty_specialist",
    "system": "system_specialist",
}

_BUILTIN_BY_NAME: dict[str, SpecialistConfig] = {
    s.name: s for s in BUILTIN_SPECIALISTS
}

SPECIALIST_MAP: dict[str, SpecialistConfig] = {
    short: _BUILTIN_BY_NAME[full]
    for short, full in SHORT_ALIASES.items()
    if full in _BUILTIN_BY_NAME
}
# Every builtin is also addressable by its full name (aliases win ties).
for _s in BUILTIN_SPECIALISTS:
    SPECIALIST_MAP.setdefault(_s.name, _s)


def resolve_specialist(key: str) -> SpecialistConfig | None:
    """Resolve a short alias or full specialist name; None if unknown."""
    return SPECIALIST_MAP.get(key)


def specialist_choices() -> list[str]:
    """Stable schema-enum order — explore/general_purpose first (they are
    the Claude Code defaults), then everything else alphabetically."""
    front = [k for k in ("explore", "general_purpose") if k in SPECIALIST_MAP]
    rest = sorted(k for k in SPECIALIST_MAP if k not in front)
    return front + rest


def _build_agent_type_roster() -> str:
    """Compile the per-specialist routing roster shipped in the dispatch
    schemas (`agent` + legacy `delegate`).

    One line per canonical builtin — primary enum key, alternate aliases
    in parens, then the ``description:`` frontmatter. Single source of
    truth: the same file that owns the ``tools:`` allowlist owns the one
    line that advertises it, so the roster cannot drift the way the
    hand-written delegate.py hint did (3 of 17 specialists by 2026-08-19,
    and the brain routed an MCP restart to a specialist with zero MCP
    tools). Descriptions are CI-gated non-empty and <= 100 chars
    (test_specialist_prompt_sweep).
    """
    aliases_by_full: dict[str, list[str]] = {}
    for short, full in SHORT_ALIASES.items():
        aliases_by_full.setdefault(full, []).append(short)

    lines: list[str] = []
    for spec in BUILTIN_SPECIALISTS:
        primary = spec.name.removesuffix("_specialist")
        if primary not in SPECIALIST_MAP:
            primary = spec.name
        others = sorted(
            a for a in aliases_by_full.get(spec.name, []) if a != primary
        )
        label = f"{primary} ({'/'.join(others)})" if others else primary
        lines.append(f"{label}: {spec.description or spec.display_name}")
    return "\n".join(lines)


# Static per process — builtins load once at import.
AGENT_TYPE_ROSTER: str = _build_agent_type_roster()


__all__ = [
    "AGENT_TYPE_ROSTER",
    "SHORT_ALIASES",
    "SPECIALIST_MAP",
    "resolve_specialist",
    "specialist_choices",
]
