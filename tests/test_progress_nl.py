"""Progress-verb extensions to the bare-NL task router.

Pins the regex coverage for "started X" / "working on X" / "stuck on X"
/ etc. — including Spanish equivalents — and verifies the
``_handle_progress`` end-to-end shape via monkeypatched tasks +
append_progress_entry.
"""
from __future__ import annotations

import pytest

from lazyclaw.channels import task_nl_router as router


def _t(tid: str, title: str, **extra) -> dict:
    return {"id": tid, "title": title, "status": "todo", **extra}


# ── Verb pre-check ─────────────────────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "started the upwork application",
    "starting upwork",
    "working on upwork section 2",
    "in the middle of dentist paperwork",
    "halfway through proposal",
    "midway through draft",
    "progress on upwork: section 2 done",
    "stuck on dentist insurance",
    "blocked on upwork pricing",
    "blocked by waiting on lawyer",
    "paused upwork",
    "pause upwork",
    "resumed upwork",
    "back on upwork after lunch",
    "back to dentist",
    "empezar upwork",
    "trabajando en upwork",
    "atorado en upwork",
    "atascado con upwork",
    "bloqueado por la entrevista",
    "retomar upwork",
])
def test_verb_pre_check_detects_progress(msg: str) -> None:
    assert router._looks_like_task_verb(msg) is True


@pytest.mark.parametrize("msg", [
    "what's new today",
    "list my tasks",
    "show me upwork",
    "search for python tutorials",
    "tell me about progress reports in general",  # neutral chat
])
def test_verb_pre_check_skips_neutral(msg: str) -> None:
    assert router._looks_like_task_verb(msg) is False


# ── Regex match shapes ─────────────────────────────────────────────────


def test_started_regex_captures_rest() -> None:
    pattern, kind = next(p for p in router._PROGRESS_VERBS if p[1] == "started")
    m = pattern.match("started the upwork application")
    assert m is not None
    assert kind == "started"
    assert "upwork" in m.group("rest")


def test_stuck_regex_captures_rest() -> None:
    pattern, kind = next(p for p in router._PROGRESS_VERBS if p[1] == "stuck")
    m = pattern.match("stuck on dentist paperwork because insurance")
    assert m is not None
    assert kind == "stuck"
    assert "dentist" in m.group("rest")


def test_working_regex_handles_on_form() -> None:
    pattern, kind = next(p for p in router._PROGRESS_VERBS if p[1] == "working")
    m = pattern.match("working on upwork section 2 of proposal")
    assert m is not None
    assert "upwork" in m.group("rest")


# ── End-to-end via monkeypatched store ─────────────────────────────────


@pytest.mark.asyncio
async def test_handle_progress_started_unique_match(monkeypatch) -> None:
    """`started X` → status flip + progress_log entry."""
    target = _t("1", "Upwork application", status="todo")

    appended: list[dict] = []
    updated: list[dict] = []

    async def fake_list_tasks(*a, **kw):
        return [target]

    async def fake_append(config, user_id, task_id, **kw):
        appended.append({"task_id": task_id, **kw})
        return {"ts": "2026-05-09T10:00:00+00:00", **kw}

    async def fake_clear_nudge(*a, **kw):
        return None

    async def fake_update(config, user_id, task_id, **fields):
        updated.append({"task_id": task_id, **fields})
        return True

    monkeypatch.setattr("lazyclaw.tasks.store.list_tasks", fake_list_tasks)
    monkeypatch.setattr(
        "lazyclaw.tasks.store.append_progress_entry", fake_append,
    )
    monkeypatch.setattr(
        "lazyclaw.tasks.store.clear_nudge_sent", fake_clear_nudge,
    )
    monkeypatch.setattr("lazyclaw.tasks.store.update_task", fake_update)

    out = await router.try_route_task_nl(
        None, "u-1", "started the upwork application",
    )
    assert out is not None
    assert "started" in out.lower()
    assert appended and appended[0]["kind"] == "started"
    assert appended[0]["source"] == "nl"
    # status flip from todo → in_progress
    assert any(u.get("status") == "in_progress" for u in updated)


@pytest.mark.asyncio
async def test_handle_progress_stuck_no_status_flip(monkeypatch) -> None:
    """`stuck on X` records the entry but doesn't flip status."""
    target = _t("1", "Upwork application", status="in_progress")
    appended: list[dict] = []
    updated: list[dict] = []

    async def fake_list_tasks(*a, **kw):
        return [target]

    async def fake_append(config, user_id, task_id, **kw):
        appended.append(kw)
        return {"ts": "x", **kw}

    async def fake_clear_nudge(*a, **kw):
        return None

    async def fake_update(config, user_id, task_id, **fields):
        updated.append(fields)
        return True

    monkeypatch.setattr("lazyclaw.tasks.store.list_tasks", fake_list_tasks)
    monkeypatch.setattr(
        "lazyclaw.tasks.store.append_progress_entry", fake_append,
    )
    monkeypatch.setattr(
        "lazyclaw.tasks.store.clear_nudge_sent", fake_clear_nudge,
    )
    monkeypatch.setattr("lazyclaw.tasks.store.update_task", fake_update)

    out = await router.try_route_task_nl(
        None, "u-1", "stuck on upwork pricing",
    )
    assert out is not None
    assert appended[0]["kind"] == "stuck"
    # No update_task call for status — only the auto-clear path may
    # have called it, but not for status flips.
    assert not any("status" in u for u in updated)


@pytest.mark.asyncio
async def test_handle_progress_ambiguous_falls_through(monkeypatch) -> None:
    """Two upwork tasks → router returns None so brain takes over."""
    async def fake_list_tasks(*a, **kw):
        return [
            _t("1", "Upwork application"),
            _t("2", "Upwork interview prep"),
        ]
    monkeypatch.setattr("lazyclaw.tasks.store.list_tasks", fake_list_tasks)

    out = await router.try_route_task_nl(
        None, "u-1", "working on upwork",
    )
    assert out is None
