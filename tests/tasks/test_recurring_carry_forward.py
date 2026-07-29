"""Recurring-respawn data-loss + subtask-cascade regressions.

Reported symptom (2026-07-26): "Recurring tasks — when I complete it, losing
all things. Also if task has subtasks and I make it complete, subtasks are
staying without check."

Root causes proven in ``lazyclaw/tasks/store.py``:

R1 — ``complete_task``'s recurring-respawn block calls ``create_task`` with
     only {title, description, category, priority, owner, due_date,
     reminder_at, recurring, tags}. Every other user-authored column is
     silently dropped: ``steps`` (the sub-task checklist), ``check_every``
     (pulse cadence), ``progress_template_id``, ``allocated_budget``. So the
     next occurrence of "water the plants" comes back with an EMPTY checklist.

R2 — ``create_task`` does not itself derive ``pre_reminders``; the REST route
     (``gateway/routes/tasks.py:create_task_route``) does it via
     ``resolve_pre_reminders`` before calling the store. The respawn bypasses
     the route, so the FIRST occurrence a user creates has advance reminders
     and EVERY subsequent occurrence has none — the user's "notifications are
     broken for recurring tasks" complaint.

R3 — ``complete_task`` flips the parent row to ``done`` but never touches the
     ``steps`` JSON, so a completed parent keeps unchecked children. Todoist,
     TickTick and Things all mark descendants complete when the parent is
     completed; leaving them unchecked also breaks the progress label
     (``subtaskProgressLabel`` renders "1/3" on a task that is done).

Semantics decided here (matching Todoist's documented recurring behaviour):
carry the checklist STRUCTURE forward to the next occurrence but RESET every
item to unchecked — a repeating task's checklist is the recipe, not the log.

Store-level tests against an isolated temp DB (never the live ``./data``).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def far_future_cron(monkeypatch):
    """Pin ``get_next_run`` to a deterministic instant ~30h out.

    The real cron would put a daily fire anywhere in the next 24h — possibly
    one minute away, which makes a ``-2h`` advance-reminder offset resolve to
    the past and drop out. Pinning it keeps the pre_reminders assertions
    deterministic.
    """
    fixed = datetime.now(timezone.utc) + timedelta(hours=30)

    def _fake_get_next_run(expr, user_id=None):  # noqa: ARG001
        return fixed

    monkeypatch.setattr(
        "lazyclaw.heartbeat.cron.get_next_run", _fake_get_next_run
    )
    return fixed


def _open_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t["status"] != "done"]


def _steps_of(task: dict) -> list[dict]:
    raw = task.get("steps")
    if not raw:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw


# ---------------------------------------------------------------------------
# R1 — the respawn must carry every user-authored field forward
# ---------------------------------------------------------------------------

async def test_respawn_carries_subtasks_reset_to_unchecked(cfg, far_future_cron) -> None:
    """The next occurrence must inherit the checklist, with every item RESET.

    A weekly "water the plants" with [kitchen, balcony] must come back next
    week with BOTH steps present and BOTH unchecked. Today the respawn drops
    ``steps`` entirely and the user has to retype the checklist every cycle.
    """
    task = await task_store.create_task(
        cfg, "u1", "water the plants",
        recurring="0 9 * * 1",
        steps=[{"title": "kitchen"}, {"title": "balcony"}],
    )
    # User ticks one of the two before completing the parent.
    original_steps = _steps_of(task)
    assert len(original_steps) == 2
    original_steps[0]["done"] = True
    await task_store.update_task(cfg, "u1", task["id"], steps=original_steps)

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1, "exactly one next occurrence should be spawned"

    next_steps = _steps_of(spawned[0])
    assert [s["title"] for s in next_steps] == ["kitchen", "balcony"], (
        "respawned occurrence lost its sub-task checklist — the user must "
        f"retype it every cycle (got {next_steps!r})"
    )
    assert all(s["done"] is False for s in next_steps), (
        "respawned checklist must start unchecked — a repeating task's "
        f"checklist is the recipe, not the log (got {next_steps!r})"
    )


async def test_respawn_carries_pulse_cadence_and_budget(cfg, far_future_cron) -> None:
    """``check_every`` / ``progress_template_id`` / ``allocated_budget`` must
    survive the respawn — they are user-authored settings, not per-occurrence
    state."""
    task = await task_store.create_task(
        cfg, "u1", "weekly report", recurring="0 9 * * 5",
    )
    await task_store.update_task(
        cfg, "u1", task["id"],
        check_every="0 */4 * * *",
        allocated_budget=250.0,
    )

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    nxt = spawned[0]
    assert nxt["check_every"] == "0 */4 * * *", (
        f"respawn dropped the pulse cadence (got {nxt['check_every']!r})"
    )
    assert nxt["allocated_budget"] == 250.0, (
        f"respawn dropped the per-task budget (got {nxt['allocated_budget']!r})"
    )


async def test_respawn_carry_columns_are_real_columns() -> None:
    """Every name in ``_RESPAWN_CARRY_COLUMNS`` must be a real task column.

    The respawn copies these with ``update_task(**carried)``, which builds
    ``SET <name> = ?`` from the key verbatim. A typo or a renamed column would
    not fail loudly — it would raise inside the respawn's broad ``except``,
    which only logs a warning, so the carry-forward would silently stop working
    again.
    """
    unknown = set(task_store._RESPAWN_CARRY_COLUMNS) - set(task_store.TASK_COLUMNS)
    assert not unknown, f"_RESPAWN_CARRY_COLUMNS names non-existent columns: {unknown}"


async def test_every_task_column_has_a_respawn_disposition() -> None:
    """Adding a task column must force a decision about what the respawn does.

    THE bug class this whole module exists for: the respawn hand-listed nine
    fields, so every column added afterwards (``steps``, ``pre_reminders``,
    ``check_every``, ``progress_template_id``, ``allocated_budget``) was
    silently dropped on completion — with the entire suite green, because
    "dropped" was the default. Three separate fixes each rescued one field
    without noticing the others.

    This test makes the default LOUD instead: every column must be classified
    as carried, derived, reset, or a ``create_task`` kwarg. A new column with no
    disposition fails here rather than quietly vanishing from users' recurring
    tasks months later.
    """
    import inspect

    columns = set(task_store.TASK_COLUMNS)
    create_kwargs = set(
        inspect.signature(task_store.create_task).parameters
    ) & columns
    carried = set(task_store._RESPAWN_CARRY_COLUMNS)
    derived = task_store._RESPAWN_DERIVED_COLUMNS
    reset = task_store._RESPAWN_RESET_COLUMNS

    unclassified = columns - create_kwargs - carried - derived - reset
    assert not unclassified, (
        "these task columns have no recurring-respawn disposition — decide "
        "whether each is carried, derived, or reset and add it to the matching "
        f"set in store.py: {sorted(unclassified)}"
    )


async def test_respawn_dispositions_do_not_overlap() -> None:
    """A column classified twice is a contradiction, not a belt-and-braces.

    ``carry`` and ``reset`` prescribe opposite behaviour; if a column appeared
    in both, the exhaustiveness test above would still pass while the intent
    was ambiguous. ``create_task`` kwargs are excluded from the comparison —
    ``pre_reminders``/``steps``/``due_date``/``reminder_at`` are legitimately
    both kwargs and recomputed per occurrence.
    """
    carried = set(task_store._RESPAWN_CARRY_COLUMNS)
    derived = set(task_store._RESPAWN_DERIVED_COLUMNS)
    reset = set(task_store._RESPAWN_RESET_COLUMNS)

    for a, b, label in (
        (carried, derived, "CARRY/DERIVED"),
        (carried, reset, "CARRY/RESET"),
        (derived, reset, "DERIVED/RESET"),
    ):
        assert not (a & b), f"columns classified in both {label}: {sorted(a & b)}"


async def test_respawn_rearms_the_pulse_job(cfg, far_future_cron) -> None:
    """Carrying ``check_every`` forward is only half a fix — the next occurrence
    needs its OWN pulse job.

    ``complete_task`` pauses pulses by matching the ``[PULSE:<task_id>:``
    prefix. The respawn has a NEW task id, so the paused rows can never fire for
    it: a task with a check-in cadence went permanently silent after its first
    completion. The respawn must mint a pulse job pointed at the new id.
    """
    from lazyclaw.tasks.pulse import create_pulse_job, find_pulse_jobs

    task = await task_store.create_task(
        cfg, "u1", "quarterly report", recurring="0 9 1 */3 *",
    )
    await task_store.update_task(
        cfg, "u1", task["id"],
        check_every="0 */6 * * *",
        progress_template_id="tpl-1",
    )
    await create_pulse_job(cfg, "u1", task["id"], "tpl-1", "0 */6 * * *")
    assert await find_pulse_jobs(cfg, "u1", task["id"]), "sanity: original armed"

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    nxt = spawned[0]
    assert nxt["check_every"] == "0 */6 * * *"
    assert nxt["progress_template_id"] == "tpl-1"

    new_pulses = await find_pulse_jobs(cfg, "u1", nxt["id"])
    assert new_pulses, (
        "the respawned occurrence has no pulse job — its check-in cadence went "
        "silent forever after the first completion"
    )


async def test_respawn_without_a_template_arms_no_pulse(cfg, far_future_cron) -> None:
    """No ``progress_template_id`` means no pulse to arm — the respawn must not
    invent one (a pulse with no template would fire and fail in the daemon)."""
    from lazyclaw.tasks.pulse import find_pulse_jobs

    task = await task_store.create_task(
        cfg, "u1", "plain recurring", recurring="0 9 * * *",
    )
    await task_store.update_task(cfg, "u1", task["id"], check_every="0 */6 * * *")

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    assert not await find_pulse_jobs(cfg, "u1", spawned[0]["id"])


# ---------------------------------------------------------------------------
# R2 — the respawn must derive advance reminders like the REST route does
# ---------------------------------------------------------------------------

async def test_respawn_derives_pre_reminders(cfg, far_future_cron) -> None:
    """The next occurrence must get advance reminders.

    ``gateway/routes/tasks.py`` calls ``resolve_pre_reminders`` before
    ``create_task``, so a user-created timed task fires T-2h/T-1h nags. The
    respawn calls ``create_task`` directly and passes nothing, so occurrence #2
    onward is silently stripped of every advance reminder.
    """
    reminder = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    task = await task_store.create_task(
        cfg, "u1", "take meds",
        recurring="0 8 * * *",
        reminder_at=reminder,
        pre_reminders=[
            (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
            (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
        ],
    )
    assert json.loads(task["pre_reminders"]), "sanity: original has advance reminders"

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    nxt = spawned[0]
    assert nxt["reminder_at"], "sanity: respawn kept its main reminder"

    raw = nxt["pre_reminders"]
    derived = json.loads(raw) if raw else []
    assert derived, (
        "respawned occurrence has NO advance reminders — the user's "
        "reminder_offsets silently stop applying after the first cycle"
    )
    # Every derived entry must sit strictly before the new reminder instant.
    base = datetime.fromisoformat(nxt["reminder_at"])
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    for entry in derived:
        dt = datetime.fromisoformat(entry)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        assert dt < base, (
            f"advance reminder {entry} is not before the reminder {nxt['reminder_at']}"
        )


async def test_respawn_pre_reminders_are_not_copied_verbatim(cfg, far_future_cron) -> None:
    """The advance reminders must be RE-DERIVED against the new reminder time,
    never copied from the completed occurrence (which would fire immediately —
    they are all in the past relative to the next cycle)."""
    reminder = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    stale = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    task = await task_store.create_task(
        cfg, "u1", "take meds",
        recurring="0 8 * * *",
        reminder_at=reminder,
        pre_reminders=[stale],
    )

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    raw = spawned[0]["pre_reminders"]
    derived = json.loads(raw) if raw else []
    assert stale not in derived, (
        "respawn copied the completed occurrence's advance reminders verbatim "
        "— they belong to last cycle and would fire on the wrong day"
    )


# ---------------------------------------------------------------------------
# R3 — completing a parent must check off its sub-tasks
# ---------------------------------------------------------------------------

async def test_complete_parent_marks_subtasks_done(cfg) -> None:
    """Completing a task must mark its whole checklist done.

    Today the parent flips to ``done`` while its children stay unchecked, so a
    finished task still renders "1/3" and the user ticks each item by hand.
    """
    task = await task_store.create_task(
        cfg, "u1", "ship the release",
        steps=[{"title": "tag"}, {"title": "build"}, {"title": "announce"}],
    )
    steps = _steps_of(task)
    steps[0]["done"] = True
    await task_store.update_task(cfg, "u1", task["id"], steps=steps)

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    done_task = await task_store.get_task(cfg, "u1", task["id"])
    assert done_task is not None
    assert done_task["status"] == "done"
    final_steps = _steps_of(done_task)
    assert len(final_steps) == 3, "completing the parent must not drop the checklist"
    assert all(s["done"] is True for s in final_steps), (
        "completing the parent left sub-tasks unchecked — the user has to tick "
        f"each one manually (got {final_steps!r})"
    )


async def test_cascade_marks_which_steps_it_auto_ticked(cfg) -> None:
    """The cascade must be RECOVERABLE, not destructive.

    Completing a parent ticks its whole checklist — but there is no reopen/undo
    anywhere in either client (zero hits for undo/reopen across
    ``mobile/lib/screens/tasks/`` and ``web/src/pages/Tasks.tsx``). So a stray
    swipe permanently erased the record of which items the user had ACTUALLY
    finished. Tagging the auto-ticked ones keeps that recoverable: a reopen can
    un-tick exactly these, and the UI can distinguish "auto-completed" from
    "you did this". Steps the user really completed must NOT be tagged.
    """
    task = await task_store.create_task(
        cfg, "u1", "ship the release",
        steps=[{"title": "tag"}, {"title": "build"}, {"title": "announce"}],
    )
    steps = _steps_of(task)
    steps[0]["done"] = True  # the user genuinely did this one
    await task_store.update_task(cfg, "u1", task["id"], steps=steps)

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    done_task = await task_store.get_task(cfg, "u1", task["id"])
    assert done_task is not None
    by_title = {s["title"]: s for s in _steps_of(done_task)}

    assert by_title["tag"]["done"] is True
    assert not by_title["tag"].get("cascaded"), (
        "a step the user completed themselves must not be tagged as auto-ticked "
        "— reopening would then wrongly un-tick real work"
    )
    for title in ("build", "announce"):
        assert by_title[title]["done"] is True
        assert by_title[title].get("cascaded") is True, (
            f"{title!r} was auto-ticked by the cascade but is not tagged, so "
            "which items the user actually finished is lost irrecoverably"
        )


async def test_cascade_tag_survives_a_later_steps_update(cfg) -> None:
    """``_normalize_steps`` must preserve the tag.

    Every steps write goes through the normaliser, which rebuilds each dict as
    ``{id, title, done}``. If it drops ``cascaded``, the recovery information
    evaporates on the next unrelated edit and the tag is decorative.
    """
    task = await task_store.create_task(
        cfg, "u1", "release", steps=[{"title": "a"}, {"title": "b"}],
    )
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    done_task = await task_store.get_task(cfg, "u1", task["id"])
    assert done_task is not None
    # Re-persist verbatim, exactly as an unrelated edit would.
    await task_store.update_task(
        cfg, "u1", task["id"], steps=_steps_of(done_task),
    )

    reread = await task_store.get_task(cfg, "u1", task["id"])
    assert reread is not None
    assert all(s.get("cascaded") is True for s in _steps_of(reread)), (
        "the normaliser stripped the cascade tag on a round-trip"
    )


async def test_complete_parent_with_no_subtasks_is_unaffected(cfg) -> None:
    """The cascade must be a no-op for a task with no checklist (no empty-list
    write that would churn ``updated_at`` or break the sync delta)."""
    task = await task_store.create_task(cfg, "u1", "simple task")
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    done_task = await task_store.get_task(cfg, "u1", task["id"])
    assert done_task is not None
    assert done_task["status"] == "done"
    assert not _steps_of(done_task)


async def test_recurring_parent_completion_cascades_and_respawn_resets(
    cfg, far_future_cron
) -> None:
    """The two rules compose: the COMPLETED occurrence keeps a fully-checked
    checklist (it is the historical record) while the NEXT one starts fresh."""
    task = await task_store.create_task(
        cfg, "u1", "weekly review",
        recurring="0 9 * * 5",
        steps=[{"title": "inbox zero"}, {"title": "plan next week"}],
    )

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    completed = await task_store.get_task(cfg, "u1", task["id"])
    assert completed is not None
    assert all(s["done"] is True for s in _steps_of(completed)), (
        "the completed occurrence should read as fully done"
    )

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    assert all(s["done"] is False for s in _steps_of(spawned[0])), (
        "the next occurrence should start with an unchecked checklist"
    )
