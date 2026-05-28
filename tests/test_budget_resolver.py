"""Tests for the precision-first expense resolver.

Mistake-free contract: NEVER auto-route on multiple matches; only auto-route
on a single match (exact / substring / fuzzy). No match → caller decides.
"""

from __future__ import annotations

import pytest

from lazyclaw.budgets.resolver import (
    resolve_project, resolve_task,
)


def _proj(name: str) -> dict:
    return {"id": f"id-{name}", "name": name, "name_key": name.lower()}


def _task(title: str, tid: str | None = None) -> dict:
    return {"id": tid or f"t-{title}", "title": title}


def test_exact_match_auto_routes():
    projects = [_proj("nima"), _proj("ninja"), _proj("mom")]
    r = resolve_project("Nima", projects)
    assert r.resolved is not None
    assert r.resolved.name == "nima"
    assert r.reason == "exact"


def test_substring_single_match_auto_routes():
    projects = [_proj("nima"), _proj("personal")]
    r = resolve_project("nim", projects)
    assert r.resolved is not None
    assert r.resolved.name == "nima"
    assert r.reason == "substring"


def test_multi_substring_match_never_auto_routes():
    """The bug-prevention contract: 'nim' matches both 'nima' and 'ninja' →
    refuse to pick."""
    projects = [_proj("nima"), _proj("ninja"), _proj("mom")]
    r = resolve_project("ni", projects)
    assert r.resolved is None
    assert r.reason == "multi"
    names = {c.name for c in r.candidates}
    assert "nima" in names and "ninja" in names


def test_fuzzy_single_match_auto_routes():
    """A typo close enough to one name (difflib ratio >= 0.85) routes safely.
    Insertions of one character keep the ratio above threshold; transpositions
    of distant chars don't — that's the precision-vs-recall tradeoff."""
    projects = [_proj("personal"), _proj("mom"), _proj("nima")]
    r = resolve_project("personall", projects)  # extra trailing l
    assert r.resolved is not None, "single trailing-char typo must still resolve"
    assert r.resolved.name == "personal"
    assert r.reason == "fuzzy"


def test_far_typo_does_not_auto_route():
    """A typo that drops too many characters should NOT auto-route. Better to
    ask back than silently log on the wrong project."""
    projects = [_proj("personal"), _proj("nima")]
    r = resolve_project("xyz", projects)
    assert r.resolved is None
    assert r.reason == "none"


def test_no_match_returns_none():
    projects = [_proj("nima")]
    r = resolve_project("xyz", projects)
    assert r.resolved is None
    assert r.reason == "none"
    assert r.candidates == ()


def test_empty_query_returns_none():
    projects = [_proj("nima")]
    r = resolve_project("  ", projects)
    assert r.resolved is None
    assert r.reason == "none"


def test_task_multi_match_returns_candidates():
    tasks = [_task("Hire painter"), _task("Pay painter"), _task("Order paint")]
    r = resolve_task("painter", tasks)
    assert r.resolved is None
    assert r.reason == "multi"
    titles = {c.title for c in r.candidates}
    assert "Hire painter" in titles and "Pay painter" in titles


def test_task_single_match_routes():
    tasks = [_task("Hire painter"), _task("Order paint")]
    r = resolve_task("hire", tasks)
    assert r.resolved is not None
    assert r.resolved.title == "Hire painter"


def test_task_exact_match_wins_over_substring():
    """If a query is an exact title AND a substring of others, exact wins."""
    tasks = [_task("paint"), _task("repaint living room")]
    r = resolve_task("paint", tasks)
    assert r.resolved is not None
    assert r.resolved.title == "paint"
    assert r.reason == "exact"
