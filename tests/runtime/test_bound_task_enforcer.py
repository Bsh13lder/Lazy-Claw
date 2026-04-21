"""Coverage for the bound-task safety net in ``process_message``.

Today a task only flips from ``todo`` → ``done``/``failed`` when a human
clicks or the agent voluntarily calls ``complete_task`` / ``fail_task``.
If a heartbeat-fired, task-bound turn ends without either call, the row
stayed ``todo`` and the mirrored LazyBrain note stayed ``📝 TODO``.

The safety net in ``lazyclaw/runtime/agent.py``:

1. Parses ``[TASK_REMINDER:<task_id>]`` at turn start.
2. Watches the turn's ``_all_tools_used`` list.
3. In the ``finally`` block, if the turn was bound and the agent never
   called a terminal task tool, calls ``fail_task(config, user_id,
   task_id, error="agent exited without marking")``.

This file also pins the PKM integrity contract in
``_mirror_status_to_lazybrain``: every status transition must produce a
visible LazyBrain update. If the mirror note is missing (create-time
``save_note`` failed silently, or user deleted the note), the mirror
heals retroactively by creating a fresh note — a silent no-op would
make the whole "second brain" feature lie to the user.

Full end-to-end coverage of ``process_message`` needs a live LLM; this
file focuses on what a regression could silently break:

* The bind regex (false-positive protection for plain ``[REMINDER]`` and
  free-chat messages, true-positive on the daemon's exact emission
  shape from ``lazyclaw/tasks/store.py:609``).
* The tool-set intersection that gates the auto-fail.
* The safety-net block's presence in the ``finally`` body (static
  source check — same regression-guard style as
  ``tests/runtime/test_stuck_escalation.py``).
* The mirror body/tag shape (so the heal path matches what
  ``create_task`` would have written).
* The heal path in ``_mirror_status_to_lazybrain`` (creates note when
  ``lazybrain_note_id`` is NULL).
* The badge strip when a prior status badge already carries an error
  payload (prevents ``❌ FAILED — A\\n\\n❌ FAILED — B\\n\\n...`` stacks
  across repeated transitions).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.runtime.agent import _TASK_REMINDER_RE
from lazyclaw.tasks.store import (
    _build_task_mirror_body,
    _build_task_mirror_tags,
    _mirror_status_to_lazybrain,
)


# ---------------------------------------------------------------------------
# 1. Bind regex — the only signal the safety net uses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,expected_id", [
    # Matches the daemon's exact emission from tasks/store.py:609
    ("[TASK_REMINDER:abc123] do the thing", "abc123"),
    # UUID-shaped id (the real shape used by tasks.store.create_task)
    (
        "[TASK_REMINDER:7f3e4d2a-1b5c-4e6f-9a8b-2c1d3e4f5a6b] pay rent",
        "7f3e4d2a-1b5c-4e6f-9a8b-2c1d3e4f5a6b",
    ),
    # Prefix with no trailing payload still binds (empty body is valid)
    ("[TASK_REMINDER:tid-42]", "tid-42"),
])
def test_task_reminder_prefix_binds(message: str, expected_id: str) -> None:
    m = _TASK_REMINDER_RE.match(message)
    assert m is not None, f"expected bind for: {message[:60]}"
    assert m.group(1) == expected_id


@pytest.mark.parametrize("message", [
    # Plain reminder (heartbeat daemon.py:222) — no id, MUST NOT bind.
    "[REMINDER] daily check-in",
    # Free chat — MUST NOT bind.
    "hello",
    "do the thing now please",
    # Prefix buried mid-string — only matches at start by design.
    "please handle this [TASK_REMINDER:abc123] later",
    # Empty message guard.
    "",
])
def test_non_task_messages_do_not_bind(message: str) -> None:
    m = _TASK_REMINDER_RE.match(message)
    assert m is None, f"expected NO bind for: {message[:60]!r}"


# ---------------------------------------------------------------------------
# 2. Terminal-tool gate — the condition that triggers auto-fail
# ---------------------------------------------------------------------------

_TERMINAL = {"complete_task", "fail_task"}


def _should_auto_fail(
    bound_task_id: str | None,
    tools_used: list[str],
) -> bool:
    """Mirror of the finally-block gate in agent.py.

    Kept in sync with the real block — if you edit one, edit the other.
    """
    if not bound_task_id:
        return False
    return not (_TERMINAL & set(tools_used))


def test_bound_turn_without_terminal_call_auto_fails() -> None:
    assert _should_auto_fail("abc123", ["search_tools", "browser"]) is True


def test_bound_turn_with_complete_task_does_not_auto_fail() -> None:
    assert _should_auto_fail(
        "abc123", ["search_tools", "complete_task"],
    ) is False


def test_bound_turn_with_fail_task_does_not_auto_fail() -> None:
    assert _should_auto_fail(
        "abc123", ["search_tools", "fail_task"],
    ) is False


def test_unbound_turn_never_auto_fails() -> None:
    # Free-chat regression guard — safety net must NOT fire when there is
    # no [TASK_REMINDER:] prefix, regardless of what tools ran.
    assert _should_auto_fail(None, []) is False
    assert _should_auto_fail(None, ["complete_task"]) is False
    assert _should_auto_fail(None, ["search_tools", "browser"]) is False


# ---------------------------------------------------------------------------
# 3. Static-source guard — confirms the safety net lives in the finally
# ---------------------------------------------------------------------------

_AGENT_PY = (
    Path(__file__).resolve().parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
)


def _agent_src() -> str:
    return _AGENT_PY.read_text(encoding="utf-8")


def test_agent_parses_bound_task_from_reminder_prefix() -> None:
    src = _agent_src()
    # Turn-start parse must use the module-level regex (not a local re.match
    # — we want the pattern reused so the test above covers production).
    assert "_bound_task_id: str | None = None" in src, (
        "bound-task variable missing from process_message — refactor "
        "must keep this name stable for the finally block to read it"
    )
    assert "_TASK_REMINDER_RE.match" in src, (
        "bound-task parse must use _TASK_REMINDER_RE so the regex "
        "coverage in this file stays load-bearing"
    )


def test_agent_finally_block_has_auto_fail_safety_net() -> None:
    src = _agent_src()
    # Load-bearing shape: the block must call fail_task with the exact
    # error string we test for in live verification (see plan).
    assert 'error="agent exited without marking"' in src, (
        "safety-net must pass the canonical error string so logs and "
        "LazyBrain badges stay greppable"
    )
    # Only-fail-never-complete invariant — complete_task must appear in
    # the gate set but NEVER as the auto-call in the safety net.
    assert '{"complete_task", "fail_task"} & set(_all_tools_used)' in src, (
        "safety-net gate must check both terminal tools; if a future "
        "refactor drops one side, unmarked turns silently drift again"
    )
    # TAOR log marker — ops greps for this to confirm the net fired.
    assert "TAOR safety net: auto-failed bound task" in src, (
        "log marker dropped — live verification step in the plan relies "
        "on `grep 'TAOR safety net' logs/` to detect false positives"
    )


# ---------------------------------------------------------------------------
# 4. PKM mirror body / tag shape — heal path must match create_task shape
# ---------------------------------------------------------------------------

def _task_dict(
    task_id: str = "tid-1",
    title: str = "fix bug",
    description: str | None = "replace the broken auth check",
    priority: str = "high",
    owner: str = "user",
    category: str | None = "work",
    due_date: str | None = "2026-04-25",
    reminder_at: str | None = None,
    recurring: str | None = None,
    tags: str | None = None,
    lazybrain_note_id: str | None = None,
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "description": description,
        "priority": priority,
        "owner": owner,
        "category": category,
        "due_date": due_date,
        "reminder_at": reminder_at,
        "recurring": recurring,
        "tags": tags,
        "lazybrain_note_id": lazybrain_note_id,
    }


def test_mirror_body_contains_title_description_and_meta() -> None:
    body = _build_task_mirror_body(_task_dict())
    assert "**Task:** fix bug" in body
    assert "replace the broken auth check" in body
    assert "priority `high`" in body
    assert "due `2026-04-25`" in body


def test_mirror_body_handles_missing_optional_fields() -> None:
    body = _build_task_mirror_body(_task_dict(
        description=None, due_date=None, reminder_at=None, recurring=None,
    ))
    assert "**Task:** fix bug" in body
    assert "priority `high`" in body
    # No crash on missing optionals — important since old rows may be
    # partially populated.
    assert "due" not in body
    assert "reminder" not in body


def test_mirror_tags_include_owner_priority_and_category() -> None:
    tags = _build_task_mirror_tags(_task_dict())
    assert "task" in tags
    assert "auto" in tags
    assert "priority/high" in tags
    assert "owner/user" in tags
    assert "category/work" in tags


def test_mirror_tags_parse_json_user_tags() -> None:
    import json as _json
    tags = _build_task_mirror_tags(_task_dict(
        tags=_json.dumps(["urgent", "billing"]),
    ))
    assert "urgent" in tags
    assert "billing" in tags


def test_mirror_tags_survive_malformed_json() -> None:
    # Defensive — a corrupt or pre-migration row must not crash mirror writes.
    tags = _build_task_mirror_tags(_task_dict(tags="not valid json"))
    assert "task" in tags
    assert "priority/high" in tags


# ---------------------------------------------------------------------------
# 5. Heal path — mirror creates note when lazybrain_note_id is NULL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mirror_heals_when_note_id_missing() -> None:
    """Contract: a status transition on a task without a mirror note
    MUST create one — the PKM is the product, silent drops break trust."""
    task = _task_dict(lazybrain_note_id=None)
    config = MagicMock()

    fake_store = MagicMock()
    fake_store.get_note = AsyncMock(return_value=None)
    fake_store.save_note = AsyncMock(return_value={
        "id": "new-note-1",
        "title": "Task: fix bug",
        "tags": ["task", "priority/high", "status/failed"],
    })
    fake_store.update_note = AsyncMock()
    fake_events = MagicMock()
    fake_events.publish_note_saved = MagicMock()

    # Patch the ``db_session`` context manager so the UPDATE statement
    # doesn't touch a real database.
    fake_db = MagicMock()
    fake_db.execute = AsyncMock()
    fake_db.commit = AsyncMock()

    class _FakeSession:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *a):
            return False

    with patch(
        "lazyclaw.lazybrain.store.get_note", fake_store.get_note,
    ), patch(
        "lazyclaw.lazybrain.store.save_note", fake_store.save_note,
    ), patch(
        "lazyclaw.lazybrain.store.update_note", fake_store.update_note,
    ), patch(
        "lazyclaw.lazybrain.events.publish_note_saved",
        fake_events.publish_note_saved,
    ), patch(
        "lazyclaw.tasks.store.db_session",
        return_value=_FakeSession(),
    ):
        await _mirror_status_to_lazybrain(
            config, "user-1", task, "failed",
            error="agent exited without marking",
        )

    # Heal must have called save_note with the failed badge in the body.
    assert fake_store.save_note.await_count == 1
    kwargs = fake_store.save_note.await_args.kwargs
    assert "❌ FAILED — agent exited without marking" in kwargs["content"]
    assert "**Task:** fix bug" in kwargs["content"]
    assert kwargs["title"] == "Task: fix bug"
    assert "status/failed" in kwargs["tags"]
    assert "priority/high" in kwargs["tags"]

    # Update path MUST NOT run — otherwise we'd write to a non-existent id.
    fake_store.update_note.assert_not_called()

    # Task row must get the new note id persisted (UPDATE tasks SET
    # lazybrain_note_id = ?) so future transitions use the update path.
    fake_db.execute.assert_awaited_once()
    sql, params = fake_db.execute.await_args.args
    assert "UPDATE tasks" in sql
    assert "lazybrain_note_id" in sql
    assert params[0] == "new-note-1"

    # In-memory task dict mutated so callers see the healed id immediately.
    assert task["lazybrain_note_id"] == "new-note-1"

    # UI event fired so the graph node appears without a reload.
    fake_events.publish_note_saved.assert_called_once()


@pytest.mark.asyncio
async def test_mirror_heals_when_note_was_deleted() -> None:
    """If the user manually deleted the mirror note, next status change
    should still produce a visible PKM entry — same heal path."""
    task = _task_dict(lazybrain_note_id="orphan-id")
    config = MagicMock()

    fake_store = MagicMock()
    # Note id exists on the task row, but the note itself was deleted.
    fake_store.get_note = AsyncMock(return_value=None)
    fake_store.save_note = AsyncMock(return_value={
        "id": "replacement-1",
        "title": "Task: fix bug",
        "tags": [],
    })
    fake_store.update_note = AsyncMock()
    fake_events = MagicMock()
    fake_events.publish_note_saved = MagicMock()

    fake_db = MagicMock()
    fake_db.execute = AsyncMock()
    fake_db.commit = AsyncMock()

    class _FakeSession:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, *a):
            return False

    with patch(
        "lazyclaw.lazybrain.store.get_note", fake_store.get_note,
    ), patch(
        "lazyclaw.lazybrain.store.save_note", fake_store.save_note,
    ), patch(
        "lazyclaw.lazybrain.store.update_note", fake_store.update_note,
    ), patch(
        "lazyclaw.lazybrain.events.publish_note_saved",
        fake_events.publish_note_saved,
    ), patch(
        "lazyclaw.tasks.store.db_session",
        return_value=_FakeSession(),
    ):
        await _mirror_status_to_lazybrain(
            config, "user-1", task, "done",
        )

    fake_store.save_note.assert_awaited_once()
    fake_store.update_note.assert_not_called()
    assert task["lazybrain_note_id"] == "replacement-1"


@pytest.mark.asyncio
async def test_mirror_updates_existing_note_without_re_creating() -> None:
    """Normal path regression guard — when the note exists, update it
    instead of creating a new one."""
    task = _task_dict(lazybrain_note_id="note-1")
    config = MagicMock()

    existing_note = {
        "id": "note-1",
        "title": "Task: fix bug",
        "content": "**Task:** fix bug\n\npriority `high`",
        "tags": ["task", "priority/high", "status/pending"],
    }

    fake_store = MagicMock()
    fake_store.get_note = AsyncMock(return_value=existing_note)
    fake_store.save_note = AsyncMock()
    fake_store.update_note = AsyncMock(return_value={
        **existing_note,
        "tags": ["task", "priority/high", "status/failed"],
    })
    fake_events = MagicMock()
    fake_events.publish_note_saved = MagicMock()

    with patch(
        "lazyclaw.lazybrain.store.get_note", fake_store.get_note,
    ), patch(
        "lazyclaw.lazybrain.store.save_note", fake_store.save_note,
    ), patch(
        "lazyclaw.lazybrain.store.update_note", fake_store.update_note,
    ), patch(
        "lazyclaw.lazybrain.events.publish_note_saved",
        fake_events.publish_note_saved,
    ):
        await _mirror_status_to_lazybrain(
            config, "user-1", task, "failed",
            error="timeout",
        )

    # Update path fired, heal path did NOT — critical to avoid note spam.
    fake_store.update_note.assert_awaited_once()
    fake_store.save_note.assert_not_called()

    kwargs = fake_store.update_note.await_args.kwargs
    assert "❌ FAILED — timeout" in kwargs["content"]
    # Prior status/pending stripped, status/failed present — exactly once.
    assert kwargs["tags"].count("status/failed") == 1
    assert "status/pending" not in kwargs["tags"]


@pytest.mark.asyncio
async def test_mirror_strips_prior_badge_with_error_payload() -> None:
    """Regression guard for a stacking bug — prior strip loop only
    matched ``❌ FAILED —\\n\\n`` (bare badge), so repeated failures with
    error messages stacked badges across the body. Heal round-tripping
    the same body must produce a single badge, not two."""
    task = _task_dict(lazybrain_note_id="note-1")
    config = MagicMock()

    body_after_first_fail = (
        "❌ FAILED — first error\n\n**Task:** fix bug\n\npriority `high`"
    )
    existing_note = {
        "id": "note-1",
        "title": "Task: fix bug",
        "content": body_after_first_fail,
        "tags": ["task", "status/failed"],
    }

    fake_store = MagicMock()
    fake_store.get_note = AsyncMock(return_value=existing_note)
    fake_store.save_note = AsyncMock()
    fake_store.update_note = AsyncMock(return_value=existing_note)
    fake_events = MagicMock()
    fake_events.publish_note_saved = MagicMock()

    with patch(
        "lazyclaw.lazybrain.store.get_note", fake_store.get_note,
    ), patch(
        "lazyclaw.lazybrain.store.save_note", fake_store.save_note,
    ), patch(
        "lazyclaw.lazybrain.store.update_note", fake_store.update_note,
    ), patch(
        "lazyclaw.lazybrain.events.publish_note_saved",
        fake_events.publish_note_saved,
    ):
        await _mirror_status_to_lazybrain(
            config, "user-1", task, "failed",
            error="second error",
        )

    kwargs = fake_store.update_note.await_args.kwargs
    new_body = kwargs["content"]
    # Exactly one FAILED badge. If the strip missed the error-carrying
    # prefix, we'd see two.
    assert new_body.count("❌ FAILED —") == 1, (
        f"badge stacked across transitions: {new_body!r}"
    )
    assert "second error" in new_body
    assert "first error" not in new_body
