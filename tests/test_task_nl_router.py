"""Bare-NL task router tests.

The router lives in ``lazyclaw/channels/task_nl_router.py`` and intercepts
common task verbs at message-start so they don't have to go through the
brain LLM. These tests pin the verb-detection + task-name extraction —
the actual skill dispatch is exercised in the per-skill tests.
"""
from __future__ import annotations

import pytest

from lazyclaw.channels import task_nl_router as router


# ── Verb pre-check (cheap filter) ──────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "snooze upwork 2h",
    "delay dentist 30m",
    "postpone Friday meeting tomorrow",
    "push the upwork task",
    "move dentist to Monday",
    "reschedule taxes",
    "complete the upwork application",
    "finish dentist",
    "mark the upwork done",
    "cancel dentist",
    "delete old task",
    "remove archived item",
    "when is dentist due?",
    "when's the upwork deadline",
    "what's the deadline on dentist",
    "set priority high on upwork",
    "priority urgent on dentist",
    "clear deadline on upwork",
    "clear the reminder on dentist",
    # Spanish equivalents
    "aplaza dentista 30 minutos",
    "cancela la cita",
    "cuándo es la cita",
    "elimina el recordatorio de dentista",
])
def test_verb_pre_check_triggers(msg: str) -> None:
    assert router._looks_like_task_verb(msg) is True


@pytest.mark.parametrize("msg", [
    "hey what's up",
    "tell me about my day",
    "list my tasks",
    "show me upwork jobs",
    "search for python tutorials",
    "what time is it",
    "",
    "   ",
])
def test_verb_pre_check_skips_chat(msg: str) -> None:
    assert router._looks_like_task_verb(msg) is False


# ── Article stripping ─────────────────────────────────────────────────


@pytest.mark.parametrize("raw, cleaned", [
    ("the upwork task", "upwork"),
    ("the upwork", "upwork"),
    ("la cita", "cita"),
    ("el recordatorio", "recordatorio"),
    ("upwork task", "upwork"),
    ("upwork", "upwork"),
])
def test_strip_articles_removes_noise(raw: str, cleaned: str) -> None:
    assert router._strip_articles(raw) == cleaned


# ── Multi-result fuzzy matcher ────────────────────────────────────────


def _t(tid: str, title: str) -> dict:
    return {"id": tid, "title": title}


def test_all_fuzzy_matches_finds_substring_hits() -> None:
    tasks = [
        _t("1", "Apply to Upwork job"),
        _t("2", "Post Upwork update"),
        _t("3", "Dentist appointment"),
    ]
    matches = router._all_fuzzy_matches(tasks, "upwork")
    assert len(matches) == 2
    ids = {m["id"] for m in matches}
    assert ids == {"1", "2"}


def test_all_fuzzy_matches_exact_short_circuits() -> None:
    """Exact match wins over substring matches."""
    tasks = [
        _t("1", "Upwork"),
        _t("2", "Upwork extras"),
    ]
    matches = router._all_fuzzy_matches(tasks, "upwork")
    assert len(matches) == 1
    assert matches[0]["id"] == "1"


def test_all_fuzzy_matches_empty_when_no_match() -> None:
    tasks = [_t("1", "Pay rent")]
    assert router._all_fuzzy_matches(tasks, "upwork") == []


# ── Name + phrase splitter ────────────────────────────────────────────


def test_split_name_and_phrase_basic() -> None:
    """'upwork 2h' → name='upwork', phrase='2h' when 'upwork' resolves."""
    tasks = [_t("1", "Apply to Upwork job")]
    name, phrase, matches = router._split_name_and_phrase("upwork 2h", tasks)
    assert name == "upwork"
    assert phrase == "2h"
    assert len(matches) == 1


def test_split_name_and_phrase_multi_word_name() -> None:
    """Longer prefix wins when it's still unambiguous."""
    tasks = [_t("1", "Dentist appointment")]
    name, phrase, matches = router._split_name_and_phrase(
        "dentist appointment Friday 10am", tasks,
    )
    # Both "dentist" and "dentist appointment" match the same single
    # task, so the longer prefix should win.
    assert "dentist appointment" in name.lower()
    assert "Friday" in phrase
    assert len(matches) == 1


def test_split_name_and_phrase_ambiguous_falls_through() -> None:
    """When the name fuzzy-matches >1 task, return them all."""
    tasks = [
        _t("1", "Upwork application"),
        _t("2", "Upwork interview prep"),
    ]
    name, phrase, matches = router._split_name_and_phrase("upwork 2h", tasks)
    # "upwork" matches both, no longer prefix resolves uniquely.
    assert len(matches) >= 2


# ── End-to-end routing ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_try_route_returns_none_for_chat(monkeypatch) -> None:
    """Plain conversational text never enters the router pipeline."""
    out = await router.try_route_task_nl(None, "u-1", "hey what's up")
    assert out is None


@pytest.mark.asyncio
async def test_try_route_returns_none_when_no_tasks(monkeypatch) -> None:
    """Empty task list → fall through to brain even on verb match."""
    async def fake_list_tasks(*args, **kwargs):
        return []
    monkeypatch.setattr(
        "lazyclaw.tasks.store.list_tasks", fake_list_tasks,
    )
    out = await router.try_route_task_nl(None, "u-1", "snooze upwork 2h")
    assert out is None


@pytest.mark.asyncio
async def test_try_route_returns_none_when_ambiguous(monkeypatch) -> None:
    """2+ matches → brain takes over (per user preference)."""
    async def fake_list_tasks(*args, **kwargs):
        return [
            _t("1", "Upwork application"),
            _t("2", "Upwork interview prep"),
        ]
    monkeypatch.setattr(
        "lazyclaw.tasks.store.list_tasks", fake_list_tasks,
    )
    out = await router.try_route_task_nl(None, "u-1", "snooze upwork 2h")
    assert out is None


@pytest.mark.asyncio
async def test_try_route_handles_complete_unique_match(monkeypatch) -> None:
    """`complete X` with one match → completes locally."""
    async def fake_list_tasks(*args, **kwargs):
        return [_t("1", "Upwork application")]

    completed: list[str] = []

    async def fake_complete(config, user_id, task_id):
        completed.append(task_id)
        return True

    monkeypatch.setattr(
        "lazyclaw.tasks.store.list_tasks", fake_list_tasks,
    )
    monkeypatch.setattr(
        "lazyclaw.tasks.store.complete_task", fake_complete,
    )
    out = await router.try_route_task_nl(
        None, "u-1", "complete the upwork application",
    )
    assert out is not None
    assert "Done" in out
    assert completed == ["1"]


@pytest.mark.asyncio
async def test_try_route_handles_delete_unique_match(monkeypatch) -> None:
    async def fake_list_tasks(*args, **kwargs):
        return [_t("9", "Old archived task")]

    deleted: list[str] = []

    async def fake_delete(config, user_id, task_id):
        deleted.append(task_id)
        return True

    monkeypatch.setattr(
        "lazyclaw.tasks.store.list_tasks", fake_list_tasks,
    )
    monkeypatch.setattr(
        "lazyclaw.tasks.store.delete_task", fake_delete,
    )
    out = await router.try_route_task_nl(
        None, "u-1", "delete old archived",
    )
    assert out is not None
    assert "Deleted" in out
    assert deleted == ["9"]
