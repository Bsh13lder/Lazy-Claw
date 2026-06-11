"""Initial-paint replay filter for the chat WS task-event pump.

On (re)connect ``chat_ws._task_event_pump`` replays recent ring-buffer
events so Activity / mobile can paint what's still running. A task that
already FINISHED must never be replayed via a non-terminal sibling alone
(``background_started`` / ``task_step`` without its terminal event) — the
client would mount a spinner that never settles.

These tests pin the pure helper ``chat_ws._initial_paint_events``:
- terminal siblings suppress their non-terminal events (same task_id);
- ``background_done`` / ``background_failed`` are never replayed;
- ``task_completed`` IS replayed (web Activity repaint back-compat);
- lost-terminal guard: a lone ``background_started`` whose id is absent
  from the runner's running set is dropped — but ONLY when the running
  set is known (``None`` = runner unwired → fail-open), and ONLY for
  ``background_started`` (TeamLead ``task_*`` ids aren't TaskRunner ids).
"""

from __future__ import annotations

import lazyclaw.gateway.routes.chat_ws as chat_ws
from lazyclaw.runtime.task_event_bus import TaskEvent


def _evt(kind: str, task_id: str) -> TaskEvent:
    return TaskEvent(user_id="u1", kind=kind, task_id=task_id, name=task_id)


# ── Terminal-sibling suppression ─────────────────────────────────────────

def test_started_with_done_sibling_replays_neither() -> None:
    events = [_evt("background_started", "bg-1"), _evt("background_done", "bg-1")]
    assert chat_ws._initial_paint_events(events, running_task_ids=None) == []


def test_teamlead_trio_replays_only_task_completed() -> None:
    events = [
        _evt("task_started", "tl-1"),
        _evt("task_step", "tl-1"),
        _evt("task_completed", "tl-1"),
    ]
    out = chat_ws._initial_paint_events(events, running_task_ids=None)
    assert [e.kind for e in out] == ["task_completed"]


# ── Lost-terminal guard (running-set check) ──────────────────────────────

def test_lone_started_in_running_set_is_replayed() -> None:
    events = [_evt("background_started", "bg-2")]
    out = chat_ws._initial_paint_events(events, running_task_ids=frozenset({"bg-2"}))
    assert [e.kind for e in out] == ["background_started"]


def test_lone_started_not_in_running_set_is_dropped() -> None:
    # Terminal sibling aged out of the ring, runner says it's gone → drop.
    events = [_evt("background_started", "bg-3")]
    assert chat_ws._initial_paint_events(events, running_task_ids=frozenset()) == []


def test_lone_started_with_unknown_running_set_is_replayed() -> None:
    # running_task_ids=None means the runner is unwired/unreachable —
    # never false-drop a genuinely running task in that case.
    events = [_evt("background_started", "bg-4")]
    out = chat_ws._initial_paint_events(events, running_task_ids=None)
    assert [e.kind for e in out] == ["background_started"]


def test_running_set_check_never_applies_to_teamlead_kinds() -> None:
    # A still-running TeamLead task has no terminal event yet AND its id
    # is not a TaskRunner id — both events must survive an empty running set.
    events = [_evt("task_started", "tl-2"), _evt("task_step", "tl-2")]
    out = chat_ws._initial_paint_events(events, running_task_ids=frozenset())
    assert [e.kind for e in out] == ["task_started", "task_step"]


# ── Existing rule pinned: bg terminals never replay ──────────────────────

def test_background_terminals_are_never_replayed() -> None:
    events = [_evt("background_done", "bg-5"), _evt("background_failed", "bg-6")]
    assert chat_ws._initial_paint_events(events, running_task_ids=None) == []
    assert chat_ws._initial_paint_events(
        events, running_task_ids=frozenset({"bg-5", "bg-6"})
    ) == []
