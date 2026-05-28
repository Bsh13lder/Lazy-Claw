"""Fuzzy resolver for expense capture — turns NL project/task names into
exact entities, never silently picks a wrong target.

Tiers (precision-first):
- ``exact``      casefold name == requested  → resolved (auto).
- ``substring``  query is contained in one name  → resolved (auto-confirm).
- ``fuzzy``      ``difflib`` ratio >= 0.85, single hit  → resolved (suggested).
- ``multi``      >1 hits at any tier  → never auto. Caller asks back.
- ``none``       no hit  → caller falls back to default / asks back.

Stdlib only (``difflib``). No new dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_MIN_RATIO = 0.85


@dataclass(frozen=True)
class ProjectHit:
    id: str
    name: str
    name_key: str


@dataclass(frozen=True)
class TaskHit:
    id: str
    title: str


@dataclass(frozen=True)
class ProjectResolve:
    resolved: ProjectHit | None
    candidates: tuple[ProjectHit, ...]
    reason: str  # exact | substring | fuzzy | multi | none


@dataclass(frozen=True)
class TaskResolve:
    resolved: TaskHit | None
    candidates: tuple[TaskHit, ...]
    reason: str


def _norm(s: str | None) -> str:
    return " ".join((s or "").strip().casefold().split())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _project_hit(p: dict) -> ProjectHit:
    return ProjectHit(id=p["id"], name=p["name"], name_key=p["name_key"])


def resolve_project(query: str, projects: list[dict]) -> ProjectResolve:
    """Match a free-text project name against the user's projects."""
    q = _norm(query)
    items = [_project_hit(p) for p in projects if p.get("name_key")]
    if not q:
        return ProjectResolve(None, (), "none")

    exact = [x for x in items if x.name_key == q]
    if len(exact) == 1:
        return ProjectResolve(exact[0], (exact[0],), "exact")
    if len(exact) > 1:
        return ProjectResolve(None, tuple(exact), "multi")

    subs = [x for x in items if q in x.name_key]
    if len(subs) == 1:
        return ProjectResolve(subs[0], (subs[0],), "substring")
    if len(subs) > 1:
        return ProjectResolve(None, tuple(subs), "multi")

    fuzzy = [x for x in items if _ratio(q, x.name_key) >= FUZZY_MIN_RATIO]
    if len(fuzzy) == 1:
        return ProjectResolve(fuzzy[0], (fuzzy[0],), "fuzzy")
    if len(fuzzy) > 1:
        return ProjectResolve(None, tuple(fuzzy), "multi")

    return ProjectResolve(None, (), "none")


def resolve_task(query: str, tasks: list[dict]) -> TaskResolve:
    """Match a free-text task name against the candidate task list. Caller
    is responsible for pre-filtering tasks to the right project."""
    q = _norm(query)
    items = [
        TaskHit(id=t["id"], title=t.get("title") or "")
        for t in tasks
        if t.get("title")
    ]
    if not q:
        return TaskResolve(None, (), "none")

    exact = [x for x in items if _norm(x.title) == q]
    if len(exact) == 1:
        return TaskResolve(exact[0], (exact[0],), "exact")
    if len(exact) > 1:
        return TaskResolve(None, tuple(exact), "multi")

    subs = [x for x in items if q in _norm(x.title)]
    if len(subs) == 1:
        return TaskResolve(subs[0], (subs[0],), "substring")
    if len(subs) > 1:
        return TaskResolve(None, tuple(subs), "multi")

    fuzzy = [x for x in items if _ratio(q, _norm(x.title)) >= FUZZY_MIN_RATIO]
    if len(fuzzy) == 1:
        return TaskResolve(fuzzy[0], (fuzzy[0],), "fuzzy")
    if len(fuzzy) > 1:
        return TaskResolve(None, tuple(fuzzy), "multi")

    return TaskResolve(None, (), "none")
