"""Research-first fan-out — LLM-specialist complement to ``plan_research``.

Where :mod:`lazyclaw.runtime.plan_research` gathers zero-LLM context (LazyBrain
semantic search, skill lessons, a cheap codebase grep, project tasks), this
module dispatches two *read-only research specialists* in parallel and returns
their synthesized findings:

  * **Code research** — greps/reads the repo and reports findings with
    ``file:line`` citations (``code_research_specialist.md``).
  * **Web research**  — searches the web/docs and reports facts with source
    URLs (``web_research_specialist.md``).

Both run as isolated specialist agent loops via
:func:`lazyclaw.teams.runner.run_specialist` (same call shape the dispatcher
uses for subagents), fanned out with :func:`asyncio.gather`. Each lane is
wrapped in its own timeout + try/except so a slow or failing lane never sinks
the other — partial results are fine. The result is a single markdown block::

    ## Research findings
    ### Code
    ...
    ### Web
    ...

or ``''`` when nothing useful came back. This is pure orchestration: it makes
zero brain-LLM calls of its own (each specialist runs on the worker role inside
``run_specialist``). It is meant to feed the Plan-mode path BEFORE the planner
LLM call, so the brain never plans from memory. See ADR-0005, Phase 4.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.skills.registry import SkillRegistry
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams.specialist import SpecialistConfig

logger = logging.getLogger(__name__)


# Per-lane wall-clock cap. Research specialists run a full agent loop (~6 tool
# calls each), so this is generous compared to plan_research's 2s zero-LLM
# lanes, while still bounding a runaway lane. Override for slow backends.
def _lane_timeout_s() -> float:
    raw = os.environ.get("LAZYCLAW_RESEARCH_LANE_TIMEOUT")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return 90.0


# Defensive cap on each lane's contribution so one verbose specialist can't
# blow up the plan prompt. Truncation is marked so the planner knows it's clipped.
_MAX_LANE_CHARS: int = 6000

_CODE_RESEARCH_MD: str = "code_research_specialist.md"
_WEB_RESEARCH_MD: str = "web_research_specialist.md"


def _load_research_specialist(
    filename: str, *, directory: Path | None = None
) -> SpecialistConfig:
    """Parse one research specialist ``.md`` from the builtin specialists dir.

    Read fresh on each fan-out (the files are tiny) so an edited definition
    takes effect without a restart. Raises ``ValueError`` / ``OSError`` on a
    missing or malformed file — callers handle that per-lane and degrade
    gracefully rather than failing the whole fan-out.

    The loader is imported lazily to avoid a module-level circular import
    (``specialist_loader`` ↔ ``specialist``); by call time both are fully
    initialised.
    """
    from lazyclaw.teams.specialist_loader import (
        BUILTIN_SPECIALISTS_DIR,
        parse_specialist_md,
    )

    base = directory or BUILTIN_SPECIALISTS_DIR
    path = Path(base) / filename
    return parse_specialist_md(path.read_text(encoding="utf-8"), is_builtin=True)


def _code_research_task(message: str) -> str:
    """Build the read-only codebase-research instruction for the message."""
    return (
        "Research THIS repository to inform the request below. Use grep/read "
        "to find the relevant modules and report findings with concrete "
        "`path/to/file.py:line` citations. Do NOT edit or write any file.\n\n"
        f"REQUEST:\n{message}"
    )


def _web_research_task(message: str) -> str:
    """Build the read-only web/docs-research instruction for the message."""
    return (
        "Research the web and official docs to inform the request below. Cite "
        "a source URL for every fact and report only what the tool results "
        "contained — never from memory. Take no stateful actions.\n\n"
        f"REQUEST:\n{message}"
    )


def _clip(text: str) -> str:
    """Trim a lane's output to ``_MAX_LANE_CHARS`` with a truncation marker."""
    stripped = text.strip()
    if len(stripped) <= _MAX_LANE_CHARS:
        return stripped
    return stripped[:_MAX_LANE_CHARS].rstrip() + "\n… [truncated]"


async def _run_research_lane(
    specialist: SpecialistConfig,
    task: str,
    *,
    user_id: str,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker: object,
    timeout: float,
) -> SpecialistResult | None:
    """Run one research specialist with a hard timeout. Failures → ``None``.

    Mirrors ``dispatcher._run_subagent``'s call into ``run_specialist`` (same
    keyword signature), but lazy-imports it so tests can monkeypatch
    ``lazyclaw.teams.runner.run_specialist``. Every failure mode (timeout,
    exception) is swallowed and logged so a bad lane can't sink the fan-out.
    """
    from lazyclaw.teams.runner import run_specialist

    try:
        return await asyncio.wait_for(
            run_specialist(
                user_id=user_id,
                specialist=specialist,
                task=task,
                registry=registry,
                eco_router=eco_router,
                permission_checker=permission_checker,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "research_fanout lane %s timed out after %.0fs",
            specialist.name, timeout,
        )
        return None
    except Exception:
        logger.debug(
            "research_fanout lane %s errored", specialist.name, exc_info=True,
        )
        return None


def _lane_finding(result: SpecialistResult | None) -> str:
    """Extract a usable findings string from a settled lane, else ``''``."""
    if result is None or not getattr(result, "success", False):
        return ""
    text = (getattr(result, "result", "") or "").strip()
    return _clip(text) if text else ""


async def gather_specialist_research(
    config: Config,
    user_id: str,
    message: str,
    *,
    registry: SkillRegistry,
    eco_router: EcoRouter,
    permission_checker: object,
    want_code: bool = True,
    want_web: bool = True,
) -> str:
    """Fan out the code + web research specialists in parallel; synthesize.

    Returns a ``## Research findings`` markdown block with a ``### Code`` and/or
    ``### Web`` section, or ``''`` when nothing useful surfaced. Pure
    orchestration — no brain-LLM call of its own. Each requested lane runs
    concurrently under its own timeout/try-except, so partial results (one lane
    succeeds, the other fails) still yield a block.
    """
    msg = (message or "").strip()
    if not msg:
        return ""

    timeout = _lane_timeout_s()

    # Build the (kind, coroutine) lanes for whatever was requested. Loading a
    # specialist definition is best-effort: a missing/broken .md drops that
    # lane rather than failing the whole fan-out.
    lanes: list[tuple[str, "Awaitable[SpecialistResult | None]"]] = []

    lane_specs: list[tuple[str, str, str]] = []
    if want_code:
        lane_specs.append(("Code", _CODE_RESEARCH_MD, _code_research_task(msg)))
    if want_web:
        lane_specs.append(("Web", _WEB_RESEARCH_MD, _web_research_task(msg)))

    for kind, filename, task in lane_specs:
        try:
            specialist = _load_research_specialist(filename)
        except (ValueError, OSError):
            logger.warning(
                "research_fanout could not load %s — skipping %s lane",
                filename, kind, exc_info=True,
            )
            continue
        lanes.append((
            kind,
            _run_research_lane(
                specialist,
                task,
                user_id=user_id,
                registry=registry,
                eco_router=eco_router,
                permission_checker=permission_checker,
                timeout=timeout,
            ),
        ))

    if not lanes:
        return ""

    # Run every lane concurrently. Lane wrappers already swallow their own
    # errors (returning None), so gather never raises here.
    results = await asyncio.gather(*(coro for _, coro in lanes))

    parts: list[str] = []
    for (kind, _), result in zip(lanes, results):
        finding = _lane_finding(result)
        if finding:
            parts.append(f"### {kind}")
            parts.append(finding)

    if not parts:
        return ""

    return "## Research findings\n" + "\n".join(parts)


__all__ = ["gather_specialist_research"]
