"""TeamLead → task_event_bus bridge.

Activity/Overview rely on the WS pump in chat_ws.py forwarding
TeamLead lifecycle events for live progress without the 3 s poll.
The bridge lives in cli._wire_team_lead_to_event_bus — these tests
pin its mapping in place.
"""

from __future__ import annotations

import pytest

from lazyclaw.cli import _wire_team_lead_to_event_bus
from lazyclaw.runtime import task_event_bus
from lazyclaw.runtime.team_lead import TeamLead


@pytest.fixture(autouse=True)
def _isolate() -> None:
    task_event_bus.clear_user("u1")
    task_event_bus.clear_user("u2")


def test_register_and_complete_emit_bridged_events() -> None:
    team_lead = TeamLead()
    _wire_team_lead_to_event_bus(team_lead, task_event_bus)

    team_lead.register(
        task_id="tl-1",
        name="research",
        description="hunt arxiv",
        lane="specialist",
        user_id="u1",
    )
    team_lead.update_step("tl-1", "web_search")
    team_lead.update_phase("tl-1", "act")
    team_lead.complete("tl-1", result_preview="ok", result_full="ok")

    events = task_event_bus.recent_events("u1", limit=10, max_age_s=None)
    kinds = [e.kind for e in events]
    assert kinds == [
        "task_started",
        "task_step",
        "task_phase",
        "task_completed",
    ]
    started, step, phase, completed = events
    assert started.task_id == "tl-1"
    assert started.lane == "specialist"
    assert step.step == "web_search"
    assert step.step_count == 1
    assert phase.phase == "act"
    assert completed.status == "done"


def test_other_user_does_not_see_events() -> None:
    team_lead = TeamLead()
    _wire_team_lead_to_event_bus(team_lead, task_event_bus)

    team_lead.register(
        task_id="iso-1",
        name="x",
        description="x",
        lane="background",
        user_id="u1",
    )
    team_lead.complete("iso-1")

    assert task_event_bus.recent_events("u1", max_age_s=None)
    assert not task_event_bus.recent_events("u2", max_age_s=None)


def test_fail_emits_status_failed_with_error() -> None:
    team_lead = TeamLead()
    _wire_team_lead_to_event_bus(team_lead, task_event_bus)

    team_lead.register(
        task_id="fl-1",
        name="bad",
        description="bad",
        lane="background",
        user_id="u1",
    )
    team_lead.fail("fl-1", error="boom")

    events = task_event_bus.recent_events("u1", limit=5, max_age_s=None)
    completed = [e for e in events if e.kind == "task_completed"]
    assert len(completed) == 1
    assert completed[0].status == "failed"
    assert completed[0].error == "boom"
