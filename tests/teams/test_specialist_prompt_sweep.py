"""Repo gate: no builtin specialist prompt may name an unreachable tool.

This is the mechanical class-killer for LazyClaw's #1 recurring specialist
incident. Five production failures share one shape — the prompt body says
"call ``X``" while ``X`` is missing from the ``tools:`` allowlist (or does
not exist at all), so the worker burns its whole budget hunting a name it
can never call:

* 2026-06-10  email_specialist   — reads but no send tool
* 2026-07-31  freelance          — ``upwork_send_message`` (×2 incidents)
* 2026-08-13  browser            — ``use_host_browser``
* 2026-08-14  messaging/email    — ``telegram_*`` / ``email_read_thread`` phantoms

The boot self-check (``startup_specialist_self_check``) only WARNS, and only
about the allowlist-vs-registry direction. THIS test is the hard gate: any
prompt↔allowlist drift fails CI, forever.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lazyclaw.teams.prompt_tool_refs import (
    SCRAPER_TOOL_NAMES,
    validate_prompt_tool_refs,
)
from lazyclaw.teams.specialist_loader import (
    BUILTIN_SPECIALISTS_DIR,
    load_builtin_specialists,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRAPER_TOOLS_DIR = _REPO_ROOT / "mcp-scraper" / "mcp_scraper" / "server_tools"


def _builtins():
    specs = load_builtin_specialists()
    assert specs, "no builtin specialists loaded — packaging bug?"
    return specs


@pytest.mark.parametrize("spec", _builtins(), ids=lambda s: s.name)
def test_builtin_prompt_names_only_reachable_tools(spec):
    violations = validate_prompt_tool_refs(spec)
    assert not violations, (
        f"{spec.name}.md prompt body tells the worker to call "
        f"{violations}, but those names are not in its `tools:` allowlist. "
        "search_tools discovery does NOT grant callability — the allowlist "
        "is the contract. Either add the tool to `tools:` (if it really "
        "exists in the registry / an mcp-* server) or fix the prompt."
    )


def test_sweep_actually_covers_every_builtin_md_file():
    """Guard the guard: the parametrised sweep must not silently shrink."""
    on_disk = {p.stem for p in BUILTIN_SPECIALISTS_DIR.glob("*.md")}
    loaded = {s.name for s in _builtins()}
    assert on_disk == loaded, (
        "builtin .md filenames and specialist names diverged — the sweep "
        "may be skipping a file"
    )


def test_scraper_tool_constant_has_no_phantom_names():
    """``SCRAPER_TOOL_NAMES`` widens the gate for ``include_scraper`` specs.

    A stale entry there would mask a genuine violation, so every name must
    still be a real ``@mcp.tool()`` in the in-repo mcp-scraper server. (A
    NEW scraper tool that we have not listed yet is harmless — it only
    means the sweep is stricter than runtime.)
    """
    if not _SCRAPER_TOOLS_DIR.is_dir():
        pytest.skip("mcp-scraper sources not present in this checkout")
    harvested: set[str] = set()
    for path in _SCRAPER_TOOLS_DIR.glob("*.py"):
        harvested |= set(
            re.findall(
                r"@mcp\.tool\([^)]*\)\s*\n\s*async def ([a-z][a-z0-9_]*)",
                path.read_text(encoding="utf-8"),
            )
        )
    assert harvested, "could not harvest any mcp-scraper tool names"
    phantom = sorted(SCRAPER_TOOL_NAMES - harvested)
    assert not phantom, f"SCRAPER_TOOL_NAMES lists non-existent tools: {phantom}"


# ── boot self-check wiring (WARNING lane, non-fatal in prod) ───────────


def test_startup_self_check_warns_on_prompt_drift(caplog, monkeypatch):
    import logging

    from lazyclaw.teams import specialist_loader
    from lazyclaw.teams import specialist as specialist_mod
    from lazyclaw.teams.specialist import SpecialistConfig

    drifted = SpecialistConfig(
        name="drifty_specialist",
        display_name="Drifty",
        system_prompt="Read with `email_read`, then send with `email_send`.",
        allowed_skills=("email_read",),
    )
    monkeypatch.setattr(specialist_mod, "BUILTIN_SPECIALISTS", (drifted,))

    class _Registry:
        def list_tools(self):
            return [{"function": {"name": "email_read"}}]

    with caplog.at_level(logging.WARNING):
        report = specialist_loader.startup_specialist_self_check(_Registry())

    # The allowlist lane's return contract is unchanged (empty = no drift).
    assert report == {}
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "email_send" in m and "prompt names tools" in m for m in messages
    ), f"no prompt-drift warning emitted: {messages}"


def test_startup_self_check_is_quiet_when_prompts_are_clean(caplog, monkeypatch):
    import logging

    from lazyclaw.teams import specialist_loader
    from lazyclaw.teams import specialist as specialist_mod
    from lazyclaw.teams.specialist import SpecialistConfig

    clean = SpecialistConfig(
        name="clean_specialist",
        display_name="Clean",
        system_prompt="Read with `email_read`.",
        allowed_skills=("email_read",),
    )
    monkeypatch.setattr(specialist_mod, "BUILTIN_SPECIALISTS", (clean,))

    class _Registry:
        def list_tools(self):
            return [{"function": {"name": "email_read"}}]

    with caplog.at_level(logging.WARNING):
        specialist_loader.startup_specialist_self_check(_Registry())
    assert not [r for r in caplog.records if "prompt" in r.getMessage().lower()]
