"""Declarative specialist loader — reads ``.md`` + frontmatter definitions.

Builtin specialists live in ``lazyclaw/teams/specialists/*.md`` as a
frontmatter block + markdown body, mirroring Claude Code's agent format.
This keeps the maintained unit a small, git-diffable, editable file
instead of a multi-kilobyte inline Python string. See ADR-0005.

Frontmatter → :class:`SpecialistConfig`::

    name            (required)            -> name
    display_name    (optional, ->name)    -> display_name
    model           (optional, ->None)    -> preferred_model
    include_scraper (optional bool, False)-> include_scraper
    tools           (optional list, ())   -> allowed_skills
    <body>          (required)            -> system_prompt

The YAML subset is parsed by :func:`lazyclaw.lazybrain.frontmatter.parse_frontmatter`
(no new dependency — same parser the LazyBrain notes use).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from lazyclaw.lazybrain.frontmatter import parse_frontmatter

if TYPE_CHECKING:  # avoid a circular import — specialist.py imports us back.
    from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)

# Builtin ``.md`` files ship inside the package (see pyproject
# ``[tool.setuptools.package-data]``). Resolve relative to this module so the
# path holds for editable installs, Docker source images, and wheels alike.
BUILTIN_SPECIALISTS_DIR = Path(__file__).parent / "specialists"


def parse_specialist_md(text: str, *, is_builtin: bool = False) -> SpecialistConfig:
    """Parse one specialist ``.md`` document into a :class:`SpecialistConfig`.

    Raises :class:`ValueError` on malformed input (no frontmatter, a
    missing/empty ``name``, or an empty body) so a broken file fails loud
    at load time rather than silently yielding a nameless specialist.
    """
    # Lazy import breaks the specialist <-> specialist_loader cycle: this
    # function is only ever called AFTER SpecialistConfig is defined (either
    # mid-import of specialist.py, or from external callers).
    from lazyclaw.teams.specialist import SpecialistConfig

    props, body, has_fm = parse_frontmatter(text)
    if not has_fm:
        raise ValueError("no `---` frontmatter block")

    raw_name = props.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("missing required non-empty `name`")
    name = raw_name.strip()

    if not body.strip():
        raise ValueError(f"specialist '{name}' has an empty system-prompt body")

    raw_display = props.get("display_name")
    display_name = (
        raw_display.strip()
        if isinstance(raw_display, str) and raw_display.strip()
        else name
    )

    raw_model = props.get("model")
    preferred_model = (
        raw_model.strip()
        if isinstance(raw_model, str) and raw_model.strip()
        else None
    )

    raw_tools = props.get("tools", [])
    if isinstance(raw_tools, list):
        allowed_skills = tuple(
            str(t).strip() for t in raw_tools if str(t).strip()
        )
    elif isinstance(raw_tools, str) and raw_tools.strip():
        allowed_skills = (raw_tools.strip(),)
    else:
        allowed_skills = ()

    include_scraper = bool(props.get("include_scraper", False))

    return SpecialistConfig(
        name=name,
        display_name=display_name,
        # Preserve the body verbatim (not stripped) so prompts stay
        # byte-identical to their authored form.
        system_prompt=body,
        allowed_skills=allowed_skills,
        preferred_model=preferred_model,
        is_builtin=is_builtin,
        include_scraper=include_scraper,
    )


def load_builtin_specialists(
    directory: Path | None = None,
) -> list[SpecialistConfig]:
    """Load every builtin specialist ``.md``, ordered by filename.

    Filename sort yields a stable order (browser, code, research). A
    malformed builtin raises — these ship with the package, so a parse
    failure is a packaging bug we want surfaced immediately, never
    swallowed.
    """
    directory = directory or BUILTIN_SPECIALISTS_DIR
    specs: list[SpecialistConfig] = []
    for path in sorted(directory.glob("*.md")):
        try:
            specs.append(
                parse_specialist_md(path.read_text(encoding="utf-8"), is_builtin=True)
            )
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc

    if not specs:
        logger.warning(
            "no builtin specialist .md files found in %s — packaging issue?",
            directory,
        )
    return specs


def validate_specialist_tools(
    specs: Iterable[SpecialistConfig],
    known_tool_names: Iterable[str],
) -> dict[str, list[str]]:
    """Find specialists whose ``tools`` reference unknown registry skills.

    Returns ``{specialist_name: [unknown tool names]}`` (empty dict = all
    valid). Dynamic scraper tools (``mcp_*_* `` / ``mcp__*``) are injected
    at runtime when ``include_scraper`` is set, so they are never flagged.
    """
    known = set(known_tool_names)
    report: dict[str, list[str]] = {}
    for s in specs:
        unknown = [
            t
            for t in s.allowed_skills
            if t not in known
            and t != "*"
            and not t.startswith("mcp_")
            and not t.startswith("mcp__")
        ]
        if unknown:
            report[s.name] = unknown
    return report


def warn_on_unknown_tools(
    specs: Iterable[SpecialistConfig],
    known_tool_names: Iterable[str],
) -> dict[str, list[str]]:
    """Validate + emit a loud WARNING per specialist with unknown tools.

    Returns the same report as :func:`validate_specialist_tools` so callers
    can decide whether to escalate. Logging-only by default — a builtin that
    drifts from the registry is surfaced, not fatal at request time.
    """
    report = validate_specialist_tools(specs, known_tool_names)
    for name, unknown in report.items():
        logger.warning(
            "specialist '%s' references unknown tools: %s "
            "(typo, unregistered skill, or disconnected MCP?)",
            name,
            ", ".join(unknown),
        )
    return report


def startup_specialist_self_check(registry) -> dict[str, list[str]]:
    """ADR-0005 startup self-check: builtin allowlists vs the LIVE registry.

    Runs after MCP servers connect so the known set covers both native
    skills and bridged MCP tools. MCP tool ids carry a ``mcp_<uuid>_``
    prefix while allowlists hold bare names (the runner matches by bare
    suffix), so both the full id and its bare form count as known.
    Logging-only — drift is surfaced loudly at boot, never fatal.
    """
    from lazyclaw.skills.tool_namespace import bare_tool_name
    from lazyclaw.teams.specialist import BUILTIN_SPECIALISTS

    known: set[str] = set()
    try:
        for t in registry.list_tools():
            name = t.get("function", {}).get("name", "")
            if name:
                known.add(name)
                known.add(bare_tool_name(name))
    except Exception:
        logger.debug("specialist self-check: registry listing failed", exc_info=True)
        return {}
    return warn_on_unknown_tools(BUILTIN_SPECIALISTS, known)


__all__ = [
    "BUILTIN_SPECIALISTS_DIR",
    "parse_specialist_md",
    "load_builtin_specialists",
    "validate_specialist_tools",
    "warn_on_unknown_tools",
    "startup_specialist_self_check",
]
