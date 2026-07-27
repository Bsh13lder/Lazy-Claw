"""Every path that reaches "done" must go through ``store.complete_task``.

The completion pipeline — recurring respawn, sub-task cascade, reminder-job
teardown, pulse pause, progress-log entry, LazyBrain mirror — lives in exactly
ONE place: ``lazyclaw/tasks/store.py::complete_task``. Several callers reached
``status='done'`` through ``update_task`` instead, which flips the column and
nothing else. Each one silently killed a recurring series:

D1 — ``PATCH /api/tasks/{id}`` with ``{"status": "done"}`` (web/mobile detail
     sheet) routed straight to ``update_task``: no next occurrence, sub-tasks
     left unchecked, and the reminder job kept firing on a finished task.
     Reverse direction too: re-opening a done task left ``completed_at``
     stamped, so the row still read as completed to anything checking it.

D2 — the agent's ``update_task`` skill accepts ``status='done'``. "mark the
     weekly review done" → the brain picks ``update_task`` instead of
     ``complete_task`` and the recurrence is destroyed. Routing (rather than
     rejecting) is the fix: a rejection message can be ignored by the brain,
     a route cannot.

D3 — ``_fuzzy_match_task`` matched on title alone. After one completion of a
     recurring task TWO rows share the title (the archived done one + the live
     next occurrence), and "delete water the plants" / "snooze water the
     plants" hit the DEAD row while the live one stayed put.

D4 — ``recurring`` was written verbatim from REST with no cron validation, so
     "daily" saved happily, rendered a "Repeats" chip, and then produced no
     next occurrence at completion time (``get_next_run`` raises → the
     respawn's broad ``except`` logs a warning and the series dies silently).

All tests run against an isolated temp DB — never the live ``./data``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
from lazyclaw.tasks import store as task_store

# A user id unique to this module: ``crypto.key_manager`` caches DEKs in a
# process-global dict keyed by user_id ONLY, so sharing "u1" with another test
# file would hand this DB the other DB's key.
UID = "u-completion-paths"
SALT = "salt-completion-paths"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (UID, "completionuser", "x", SALT),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def client(cfg, monkeypatch):
    """TestClient over the tasks router, pinned to the temp-DB config."""
    import lazyclaw.gateway.routes.tasks as routes_mod

    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(routes_mod.router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=UID, username="completionuser", display_name=None,
        encryption_salt=SALT, role="user",
    )
    return TestClient(app)


@pytest.fixture
def far_future_cron(monkeypatch):
    """Pin ``get_next_run`` ~30h out so respawn assertions are deterministic
    (a real daily cron can fire one minute from now, which makes advance
    reminders resolve into the past and drop out)."""
    fixed = datetime.now(timezone.utc) + timedelta(hours=30)

    def _fake_get_next_run(expr, after=None, user_id=None):  # noqa: ARG001
        return fixed

    monkeypatch.setattr("lazyclaw.heartbeat.cron.get_next_run", _fake_get_next_run)
    return fixed


def _open_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t["status"] != "done"]


def _steps_of(task: dict) -> list[dict]:
    raw = task.get("steps")
    if not raw:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw


# ---------------------------------------------------------------------------
# D1 — PATCH status='done' must run the completion pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_status_done_respawns_recurring_series(
    cfg, client, far_future_cron
) -> None:
    """Ticking a recurring task off from the detail sheet must spawn the next
    occurrence. Today the series dies on its first completion."""
    task = await task_store.create_task(
        cfg, UID, "water the plants", recurring="0 9 * * 1",
    )

    r = client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "done"

    live = _open_tasks(await task_store.list_tasks(cfg, UID))
    assert len(live) == 1, (
        "PATCH status='done' did not respawn the recurring occurrence — the "
        f"series is dead after one completion (live rows: {live!r})"
    )
    assert live[0]["recurring"] == "0 9 * * 1"


@pytest.mark.asyncio
async def test_patch_status_done_cascades_subtasks_and_kills_reminder_job(
    cfg, client
) -> None:
    """The sub-task cascade + reminder teardown live in ``complete_task``; a
    PATCH-completed task kept unchecked children and a live reminder job."""
    reminder = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    task = await task_store.create_task(
        cfg, UID, "ship the release",
        reminder_at=reminder,
        steps=[{"title": "tag"}, {"title": "announce"}],
    )
    assert task["reminder_job_id"], "sanity: reminder job armed"

    r = client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert r.status_code == 200, r.text

    done = await task_store.get_task(cfg, UID, task["id"])
    assert done is not None
    assert all(s["done"] is True for s in _steps_of(done)), (
        "PATCH-completing left sub-tasks unchecked — the finished task still "
        f"renders its progress label as 1/2 (got {_steps_of(done)!r})"
    )
    assert not done["reminder_job_id"], (
        "PATCH-completing left the reminder job armed — a done task keeps "
        "nagging the user"
    )


@pytest.mark.asyncio
async def test_patch_status_done_with_other_fields_applies_both(
    cfg, client, far_future_cron
) -> None:
    """A PATCH may carry the done flag alongside edits. Both must land, and the
    respawn must be built from the POST-edit row."""
    task = await task_store.create_task(
        cfg, UID, "weekly review", recurring="0 9 * * 5", priority="low",
    )

    r = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "done", "priority": "high", "title": "weekly review v2"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["task"]
    assert body["status"] == "done"
    assert body["priority"] == "high"
    assert body["title"] == "weekly review v2"

    live = _open_tasks(await task_store.list_tasks(cfg, UID))
    assert len(live) == 1
    assert live[0]["title"] == "weekly review v2", (
        "the respawn used the pre-PATCH row — edits sent with the done flag "
        f"were lost on the next occurrence (got {live[0]['title']!r})"
    )
    assert live[0]["priority"] == "high"


@pytest.mark.asyncio
async def test_patch_status_done_is_idempotent(cfg, client, far_future_cron) -> None:
    """At-least-once mobile sync re-pushes the op; a second PATCH must not
    spawn a DUPLICATE next occurrence."""
    task = await task_store.create_task(
        cfg, UID, "take out the bins", recurring="0 9 * * 3",
    )
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 200
    assert client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 200

    live = _open_tasks(await task_store.list_tasks(cfg, UID))
    assert len(live) == 1, f"duplicate respawn from a replayed PATCH: {live!r}"


@pytest.mark.asyncio
async def test_patch_status_done_on_missing_task_returns_404(client) -> None:
    r = client.patch("/api/tasks/does-not-exist", json={"status": "done"})
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_patch_reopen_clears_completed_at(cfg, client) -> None:
    """Moving a task OFF done must clear ``completed_at``. It stayed stamped,
    so a re-opened task still read as completed to every consumer of that
    column (LazyBrain mirror body, reports, the mobile "completed" filter)."""
    task = await task_store.create_task(cfg, UID, "fix the sink")
    assert await task_store.complete_task(cfg, UID, task["id"]) is True
    assert (await task_store.get_task(cfg, UID, task["id"]))["completed_at"]

    r = client.patch(f"/api/tasks/{task['id']}", json={"status": "todo"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "todo"
    assert not r.json()["task"]["completed_at"], (
        "re-opened task kept its completed_at stamp — it still reads as done "
        f"(got {r.json()['task']['completed_at']!r})"
    )


# ---------------------------------------------------------------------------
# D4 — ``recurring`` must be cron-validated at the REST write boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_rejects_human_phrase_recurring(client) -> None:
    """"daily" is not a cron expression. Accepting it renders a Repeats chip on
    a task that will never respawn — a silent lie."""
    r = client.post("/api/tasks", json={"title": "stretch", "recurring": "daily"})
    assert r.status_code == 400, r.text
    assert "recurring" in r.text.lower() or "cron" in r.text.lower()


@pytest.mark.asyncio
async def test_post_accepts_valid_cron(client) -> None:
    r = client.post("/api/tasks", json={"title": "stretch", "recurring": "0 9 * * 1"})
    assert r.status_code == 200, r.text
    assert r.json()["task"]["recurring"] == "0 9 * * 1"


@pytest.mark.asyncio
async def test_patch_rejects_human_phrase_recurring(cfg, client) -> None:
    task = await task_store.create_task(cfg, UID, "stretch")
    r = client.patch(f"/api/tasks/{task['id']}", json={"recurring": "every Monday"})
    assert r.status_code == 400, r.text

    unchanged = await task_store.get_task(cfg, UID, task["id"])
    assert not unchanged["recurring"], "the invalid value was persisted anyway"


@pytest.mark.asyncio
async def test_patch_empty_recurring_clears_the_recurrence(cfg, client) -> None:
    """Documented contract (routes/tasks.py): an EMPTY string deliberately
    CLEARS the recurrence. Validation must not break it."""
    task = await task_store.create_task(cfg, UID, "stretch", recurring="0 9 * * 1")
    r = client.patch(f"/api/tasks/{task['id']}", json={"recurring": ""})
    assert r.status_code == 200, r.text
    assert not r.json()["task"]["recurring"]


@pytest.mark.asyncio
async def test_patch_null_recurring_clears_the_recurrence(cfg, client) -> None:
    """An explicit ``null`` clears it too (PATCH null-vs-absent contract)."""
    task = await task_store.create_task(cfg, UID, "stretch", recurring="0 9 * * 1")
    r = client.patch(f"/api/tasks/{task['id']}", json={"recurring": None})
    assert r.status_code == 200, r.text
    assert not r.json()["task"]["recurring"]


# ---------------------------------------------------------------------------
# D2 — the agent's update_task skill must not bypass the pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_skill_done_respawns_recurring(cfg, far_future_cron) -> None:
    """"mark the weekly review done" via ``update_task`` must respawn.

    The brain picks whichever tool it likes; if ``update_task`` can kill a
    recurring series then the series' survival is a coin flip.
    """
    from lazyclaw.skills.builtin.task_manager import UpdateTaskSkill

    task = await task_store.create_task(
        cfg, UID, "weekly review", recurring="0 9 * * 5",
        steps=[{"title": "inbox zero"}],
    )

    out = await UpdateTaskSkill(cfg).execute(
        UID, {"task_name": "weekly review", "status": "done"},
    )
    assert "weekly review" in out

    live = _open_tasks(await task_store.list_tasks(cfg, UID))
    assert len(live) == 1, (
        "update_task(status='done') killed the recurring series — no next "
        f"occurrence was spawned (live rows: {live!r})"
    )
    completed = await task_store.get_task(cfg, UID, task["id"])
    assert completed["status"] == "done"
    assert all(s["done"] is True for s in _steps_of(completed)), (
        "update_task(status='done') skipped the sub-task cascade"
    )
    assert all(s["done"] is False for s in _steps_of(live[0]))


@pytest.mark.asyncio
async def test_update_task_skill_done_with_other_fields(cfg, far_future_cron) -> None:
    """Other fields in the same call must still be applied before completing."""
    from lazyclaw.skills.builtin.task_manager import UpdateTaskSkill

    task = await task_store.create_task(
        cfg, UID, "monthly invoice", recurring="0 9 1 * *", priority="low",
    )
    await UpdateTaskSkill(cfg).execute(
        UID, {"task_name": "monthly invoice", "status": "done", "priority": "urgent"},
    )

    completed = await task_store.get_task(cfg, UID, task["id"])
    assert completed["priority"] == "urgent"
    assert completed["status"] == "done"
    live = _open_tasks(await task_store.list_tasks(cfg, UID))
    assert len(live) == 1
    assert live[0]["priority"] == "urgent"


@pytest.mark.asyncio
async def test_update_task_skill_reopen_clears_completed_at(cfg) -> None:
    """Symmetry with the REST route: status off done clears the stamp."""
    from lazyclaw.skills.builtin.task_manager import UpdateTaskSkill

    task = await task_store.create_task(cfg, UID, "fix the sink")
    assert await task_store.complete_task(cfg, UID, task["id"]) is True

    await UpdateTaskSkill(cfg).execute(
        UID, {"task_name": "fix the sink", "status": "todo"},
    )
    reopened = await task_store.get_task(cfg, UID, task["id"])
    assert reopened["status"] == "todo"
    assert not reopened["completed_at"], (
        f"re-opened task kept completed_at={reopened['completed_at']!r}"
    )


@pytest.mark.asyncio
async def test_update_task_skill_non_status_edit_still_works(cfg) -> None:
    """Regression guard: the ordinary edit path must be untouched."""
    from lazyclaw.skills.builtin.task_manager import UpdateTaskSkill

    task = await task_store.create_task(cfg, UID, "call dentist")
    out = await UpdateTaskSkill(cfg).execute(
        UID, {"task_name": "call dentist", "priority": "high"},
    )
    assert "call dentist" in out
    refreshed = await task_store.get_task(cfg, UID, task["id"])
    assert refreshed["priority"] == "high"
    assert refreshed["status"] == "todo"


# ---------------------------------------------------------------------------
# D3 — fuzzy matching must prefer the LIVE occurrence
# ---------------------------------------------------------------------------


def test_fuzzy_match_prefers_live_over_completed() -> None:
    """After one completion of a recurring task, two rows share the title.
    ``delete``/``snooze``/``update`` must target the LIVE one."""
    tasks = [
        {"id": "dead", "title": "water the plants", "status": "done"},
        {"id": "live", "title": "water the plants", "status": "todo"},
    ]
    assert _fuzzy_match_task(tasks, "water the plants")["id"] == "live"
    # Partial/contains match takes the same route.
    assert _fuzzy_match_task(tasks, "water")["id"] == "live"


def test_fuzzy_match_prefers_soonest_due_among_live() -> None:
    tasks = [
        {"id": "later", "title": "water the plants", "status": "todo",
         "due_date": "2026-09-01"},
        {"id": "sooner", "title": "water the plants", "status": "in_progress",
         "due_date": "2026-08-01"},
        {"id": "undated", "title": "water the plants", "status": "todo"},
    ]
    assert _fuzzy_match_task(tasks, "water the plants")["id"] == "sooner"


def test_fuzzy_match_falls_back_to_completed_when_nothing_live() -> None:
    """``/progress <task>`` and the LazyBrain lookups deliberately read
    completed tasks — a done-only list must still resolve."""
    tasks = [{"id": "dead", "title": "water the plants", "status": "done"}]
    assert _fuzzy_match_task(tasks, "water the plants")["id"] == "dead"


def test_fuzzy_match_exact_title_still_beats_a_live_substring_hit() -> None:
    """Tier order is unchanged: an exact title match wins over a contains hit
    even when the contains hit is live."""
    tasks = [
        {"id": "substring", "title": "water the plants today", "status": "todo"},
        {"id": "exact", "title": "water the plants", "status": "todo"},
    ]
    assert _fuzzy_match_task(tasks, "water the plants")["id"] == "exact"


def test_fuzzy_match_no_hit_returns_none() -> None:
    assert _fuzzy_match_task([{"id": "a", "title": "buy milk", "status": "todo"}], "zzz") is None


@pytest.mark.asyncio
async def test_delete_skill_targets_the_live_occurrence(cfg, far_future_cron) -> None:
    """End-to-end: complete a recurring task, then "delete water the plants".
    The DEAD row was deleted and the live one stayed — the user re-issued the
    command and it "did nothing"."""
    from lazyclaw.skills.builtin.task_manager import DeleteTaskSkill

    task = await task_store.create_task(
        cfg, UID, "water the plants", recurring="0 9 * * 1",
    )
    assert await task_store.complete_task(cfg, UID, task["id"]) is True
    live_before = _open_tasks(await task_store.list_tasks(cfg, UID))
    assert len(live_before) == 1, "sanity: respawn happened"

    await DeleteTaskSkill(cfg).execute(UID, {"task_name": "water the plants"})

    assert not _open_tasks(await task_store.list_tasks(cfg, UID)), (
        "delete hit the archived occurrence — the live one is still on the list"
    )
