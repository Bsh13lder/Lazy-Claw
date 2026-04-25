"""Task → LazyBrain mirror behaviour.

The mirror lives in ``lazyclaw/tasks/store.py`` and is fire-and-forget
on exception. These tests pin:

  1. Body builder includes title + description + meta + completed_at + last_error.
  2. Tag builder includes status-free base tags; caller stamps status.
  3. Mirror updates an existing note in-place when ``lazybrain_note_id`` is set.
  4. Mirror heals (creates fresh note + writes back the id) when the note is missing.
  5. Mirror failures DO NOT bubble — they log a warning instead.

We don't run the full ``complete_task`` round-trip here (that requires
the encryption stack); instead we monkey-patch the LazyBrain store the
mirror talks to. That isolates the logic the bug report cared about
("designed like that … breaking silently") without dragging in DEK
derivation.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from lazyclaw.config import Config
from lazyclaw.tasks import store as task_store


# ─── Synchronous helpers ──────────────────────────────────────────────


def test_body_includes_title_description_meta() -> None:
    body = task_store._build_task_mirror_body({
        "id": "t1",
        "title": "Pay rent",
        "description": "April",
        "priority": "high",
        "due_date": "2026-04-30",
        "reminder_at": "2026-04-30T09:00:00+00:00",
    })
    assert "**Task:** Pay rent" in body
    assert "April" in body
    assert "priority `high`" in body
    assert "due `2026-04-30`" in body
    assert "reminder `2026-04-30T09:00:00+00:00`" in body


def test_body_includes_completed_at_when_done() -> None:
    body = task_store._build_task_mirror_body({
        "id": "t1",
        "title": "Pay rent",
        "priority": "medium",
        "completed_at": "2026-04-25T12:00:00+00:00",
    })
    assert "completed `2026-04-25T12:00:00+00:00`" in body


def test_body_surfaces_last_error_block() -> None:
    body = task_store._build_task_mirror_body({
        "id": "t1",
        "title": "Run nightly job",
        "priority": "medium",
        "last_error": "ConnectionTimeout: upstream dead",
    })
    assert "**Last error:** ConnectionTimeout: upstream dead" in body


def test_tags_include_owner_priority_category_and_custom() -> None:
    tags = task_store._build_task_mirror_tags({
        "id": "t1",
        "owner": "user",
        "priority": "urgent",
        "category": "finance",
        "tags": '["bills", "month-end"]',
    })
    assert "task" in tags
    assert "auto" in tags
    assert "owner/user" in tags
    assert "priority/urgent" in tags
    assert "category/finance" in tags
    assert "bills" in tags
    assert "month-end" in tags
    # status/* is appended by the caller, NOT the base builder.
    assert not any(t.startswith("status/") for t in tags)


def test_tags_handle_malformed_tags_json_without_raising() -> None:
    tags = task_store._build_task_mirror_tags({
        "id": "t1",
        "owner": "user",
        "priority": "low",
        "tags": "{this is not json",
    })
    # Base tags still present even though custom tags couldn't parse.
    assert "task" in tags
    assert "owner/user" in tags
    assert "priority/low" in tags


# ─── Mirror behaviour with a fake LazyBrain store ─────────────────────


class _FakeLBStore:
    """Drop-in replacement for ``lazyclaw.lazybrain.store`` exposing only the
    three calls the mirror touches: ``get_note``, ``save_note``, ``update_note``.
    """

    def __init__(self, *, get_returns: dict | None = None) -> None:
        self._get_returns = get_returns
        self.saved: list[dict] = []
        self.updated: list[dict] = []

    async def get_note(self, config: Any, user_id: str, note_id: str | None):
        return self._get_returns

    async def save_note(self, config, user_id, *, content, title, tags, importance=5):
        note = {
            "id": f"new-note-{len(self.saved) + 1}",
            "user_id": user_id,
            "content": content,
            "title": title,
            "tags": list(tags),
            "importance": importance,
        }
        self.saved.append(note)
        return note

    async def update_note(self, config, user_id, note_id, *, content=None, tags=None, **_):
        note = {
            "id": note_id,
            "user_id": user_id,
            "content": content,
            "tags": list(tags) if tags is not None else None,
        }
        self.updated.append(note)
        return note


class _FakeEvents:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish_note_saved(self, *args, **kwargs) -> None:
        self.published.append((args, kwargs))


@pytest.fixture
def patch_lb(monkeypatch):
    """Swap the LazyBrain modules the mirror imports in-function.

    ``from lazyclaw.lazybrain import store as lb_store`` resolves first
    against the package's already-bound ``store`` attribute (set by
    earlier imports in the test session) and only falls back to
    ``sys.modules``. Patch both so we win regardless of test order.
    """
    import sys
    import lazyclaw.lazybrain as lb_pkg

    fake_store = _FakeLBStore()
    fake_events = _FakeEvents()

    monkeypatch.setitem(sys.modules, "lazyclaw.lazybrain.store", fake_store)
    monkeypatch.setitem(sys.modules, "lazyclaw.lazybrain.events", fake_events)
    monkeypatch.setattr(lb_pkg, "store", fake_store, raising=False)
    monkeypatch.setattr(lb_pkg, "events", fake_events, raising=False)

    # The mirror also opens a db_session to write the healed note id back —
    # neutralise that for the heal test by stubbing it.
    class _NullDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, *a, **kw):
            return None

        async def commit(self):
            return None

    def _null_db_session(_config):
        return _NullDB()

    monkeypatch.setattr(task_store, "db_session", _null_db_session)

    return fake_store, fake_events


@pytest.mark.asyncio
async def test_mirror_updates_existing_note_in_place(patch_lb) -> None:
    fake_store, _ = patch_lb
    fake_store._get_returns = {
        "id": "note-1",
        "title": "Task: Pay rent",
        "content": "**Task:** Pay rent\n\npriority `high`",
        "tags": ["task", "auto", "owner/user", "priority/high", "status/pending"],
    }

    cfg = Config(database_dir=None)
    task = {
        "id": "t-1",
        "title": "Pay rent",
        "priority": "high",
        "owner": "user",
        "lazybrain_note_id": "note-1",
        "completed_at": "2026-04-25T12:00:00+00:00",
    }
    await task_store._mirror_status_to_lazybrain(cfg, "user-1", task, "done")

    assert len(fake_store.updated) == 1
    upd = fake_store.updated[0]
    assert upd["id"] == "note-1"
    assert "✅ DONE" in upd["content"]
    assert "status/done" in upd["tags"]
    assert "status/pending" not in upd["tags"]


@pytest.mark.asyncio
async def test_mirror_heals_when_note_missing(patch_lb) -> None:
    fake_store, _ = patch_lb
    fake_store._get_returns = None  # note was deleted / never created

    cfg = Config(database_dir=None)
    task = {
        "id": "t-2",
        "title": "Send invoice",
        "priority": "medium",
        "owner": "user",
        "lazybrain_note_id": None,
        "completed_at": "2026-04-25T12:00:00+00:00",
    }
    await task_store._mirror_status_to_lazybrain(cfg, "user-1", task, "done")

    # Heal path: a new note was saved (no update).
    assert len(fake_store.saved) == 1
    assert len(fake_store.updated) == 0
    saved = fake_store.saved[0]
    assert saved["title"] == "Task: Send invoice"
    assert "✅ DONE" in saved["content"]
    assert "status/done" in saved["tags"]
    # The mirror writes the new note id back into the in-memory task dict.
    assert task["lazybrain_note_id"] == saved["id"]


@pytest.mark.asyncio
async def test_failed_status_records_error_in_body(patch_lb) -> None:
    fake_store, _ = patch_lb
    fake_store._get_returns = None  # heal path so we see the body builder output

    cfg = Config(database_dir=None)
    task = {
        "id": "t-3",
        "title": "Run cron",
        "priority": "high",
        "owner": "agent",
        "last_error": "TimeoutError: 30s",
    }
    await task_store._mirror_status_to_lazybrain(
        cfg, "user-1", task, "failed", error="TimeoutError: 30s",
    )

    saved = fake_store.saved[0]
    assert "❌ FAILED" in saved["content"]
    assert "TimeoutError: 30s" in saved["content"]
    assert "status/failed" in saved["tags"]


@pytest.mark.asyncio
async def test_mirror_exception_is_logged_at_warning(monkeypatch, caplog) -> None:
    """If lb_store.save_note blows up, the mirror logs a WARNING instead of
    silently swallowing — that's the visibility fix in this PR."""
    import sys
    import lazyclaw.lazybrain as lb_pkg

    class _BoomStore:
        async def get_note(self, *a, **kw):
            return None

        async def save_note(self, *a, **kw):
            raise RuntimeError("disk full")

        async def update_note(self, *a, **kw):
            return None

    boom = _BoomStore()
    fake_events = _FakeEvents()
    monkeypatch.setitem(sys.modules, "lazyclaw.lazybrain.store", boom)
    monkeypatch.setitem(sys.modules, "lazyclaw.lazybrain.events", fake_events)
    monkeypatch.setattr(lb_pkg, "store", boom, raising=False)
    monkeypatch.setattr(lb_pkg, "events", fake_events, raising=False)

    cfg = Config(database_dir=None)
    task = {"id": "t-4", "title": "X", "priority": "low", "owner": "user"}

    with caplog.at_level(logging.WARNING, logger="lazyclaw.tasks.store"):
        # Must not raise, even though save_note explodes.
        await task_store._mirror_status_to_lazybrain(cfg, "user-1", task, "done")

    assert any(
        "lazybrain status mirror failed" in rec.message
        for rec in caplog.records
    ), "expected a WARNING-level mirror-failure log entry"
