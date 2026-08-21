"""Site-memory injection gating (2026-08-21 learning-machinery audit).

The himap incident (2026-08-20): stale ACTION recipes — a
``site_learning`` "WORKS: ...publish..." flow and a ``compiled_path``
"PATH: publish one live blog post" — were injected into a READ-ONLY
task's tool results and read as a page-embedded prompt-injection attack.
The store has no working expiry (mark_failed's only caller is itself
uncalled; recall() refreshes last_used just by injecting), and compiled
paths are write-only (the replay machinery has zero callers).

Fixes: read/open tool results only carry navigation-class memories;
recall() gets an updated_at age cutoff; compiled paths stop being
written; success-recipe ("WORKS:") learnings stop being saved (AVOID/
error-pattern entries — the genuinely useful class — still save).
Template runs get instrumented so their value is finally measurable.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from lazyclaw.browser.site_memory import filter_navigation_memories

_REPO = Path(__file__).resolve().parents[2]

_MEMS = {
    "site_learning": [{"title": "WORKS: open add form", "content": "x"}],
    "compiled_path": [{"title": "PATH: publish a post", "content": "y"}],
    "navigation": [{"title": "Blog admin list", "content": "/admin/"}],
    "login_flow": [{"title": "Login", "content": "z"}],
    "custom": [{"title": "Learned: user prefers ES", "content": "w"}],
}


def test_action_recipes_filtered_out_for_read_contexts() -> None:
    kept = filter_navigation_memories(_MEMS)
    assert "site_learning" not in kept
    assert "compiled_path" not in kept
    assert set(kept) == {"navigation", "login_flow", "custom"}


def test_read_open_injects_only_navigation_memories() -> None:
    src = (
        _REPO / "lazyclaw" / "skills" / "builtin" / "browser_actions"
        / "read_open.py"
    ).read_text(encoding="utf-8")
    assert src.count("filter_navigation_memories(") >= 2, (
        "both the read and open injection sites must drop action recipes "
        "— un-gated injection is the himap false-injection incident"
    )


def test_recall_has_an_age_cutoff() -> None:
    from lazyclaw.browser import site_memory as sm

    src = inspect.getsource(sm.recall)
    assert "updated_at" in src and "-90 day" in src, (
        "entries must age out on updated_at — last_used refreshes just "
        "by being injected, so it can never mark staleness"
    )


def test_compiled_paths_are_no_longer_written() -> None:
    src = (_REPO / "lazyclaw" / "teams" / "learning.py").read_text(
        encoding="utf-8",
    )
    assert "save_compiled_path" not in src, (
        "compiled paths are write-only (replay has zero callers); their "
        "only runtime effect was polluting tool results"
    )


def test_success_recipes_are_not_saved() -> None:
    from lazyclaw.teams.learning import _keep_learning

    class _L:
        def __init__(self, title):
            self.title = title

    assert _keep_learning(_L("AVOID: clicking Apply opens a modal")) is True
    assert _keep_learning(_L("Error pattern: TimeoutError on submit")) is True
    assert _keep_learning(_L("WORKS: start() -> open(add/) -> click()")) is False


def test_template_runs_are_recorded() -> None:
    skill_src = (
        _REPO / "lazyclaw" / "skills" / "builtin"
        / "browser_templates_skill.py"
    ).read_text(encoding="utf-8")
    assert "record_template_run(" in skill_src, (
        "run_count previously counted auto-saves, not runs — template "
        "value was unmeasurable"
    )
    route_src = (
        _REPO / "lazyclaw" / "gateway" / "routes" / "browser_templates.py"
    ).read_text(encoding="utf-8")
    assert "record_template_run(" in route_src
