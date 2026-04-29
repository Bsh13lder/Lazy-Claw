"""Verification pump — quiet-turn auto-promote, /confirm, /reject, demote.

Borrowed Logseq DOING→DONE rotation. Tests cover the four public paths:
  1. ``run_verification_pump`` — bulk promote pending → verified after
     QUIET_TURNS_FOR_VERIFY clean turns elapsed.
  2. ``confirm_latest_shape`` — manual /confirm shortcut.
  3. ``reject_latest_shape`` — manual /reject flips to known-bad.
  4. ``demote_on_failure`` — verified card flips back to pending after
     a fresh tool failure on the same key.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from lazyclaw.runtime import lesson_verifier as ver
from lazyclaw.runtime import turn_counter as tc
from lazyclaw.runtime.skill_lesson import (
    OUTCOME_KNOWN_BAD,
    OUTCOME_PENDING,
    OUTCOME_VERIFIED,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Fake DB session with notes table semantics ─────────────────────


class _FakeRows:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Bare-minimum aiosqlite stand-in. Notes are dicts; tags is a JSON array."""

    def __init__(self, notes):
        self._notes = notes  # list of dicts

    async def execute(self, sql, params=()):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT id FROM notes"):
            user_id = params[0]
            matched = []
            for n in self._notes:
                if n["user_id"] != user_id:
                    continue
                tags_raw = n.get("tags") or "[]"
                if "kind/shape" not in tags_raw:
                    continue
                if "outcome/pending" not in tags_raw:
                    continue
                matched.append((n["id"],))
            return _FakeRows(matched)
        if sql_norm.startswith("SELECT id, tags FROM notes WHERE user_id = ? AND tags LIKE"):
            # Pick a kind/shape note. Sort by updated_at desc (input is already ordered).
            matched = []
            for n in self._notes:
                if n["user_id"] != params[0]:
                    continue
                tags_raw = n.get("tags") or "[]"
                if "kind/shape" not in tags_raw:
                    continue
                matched.append((n["id"], tags_raw))
            return _FakeRows(matched)
        if sql_norm.startswith(
            "SELECT id, tags FROM notes WHERE user_id = ? AND title_key = ?"
        ):
            user_id, title_key = params
            for n in self._notes:
                if (
                    n["user_id"] == user_id
                    and n.get("title_key") == title_key
                    and "outcome/verified" in (n.get("tags") or "")
                ):
                    return _FakeRows([(n["id"], n["tags"])])
            return _FakeRows([])
        raise NotImplementedError(f"unhandled SQL: {sql!r}")

    async def commit(self):
        return None


class _DBContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _seed_pending(turn_when_pending: int) -> dict:
    """Build a fake-DB note that's `kind/shape` + `outcome/pending`."""
    fm = (
        "---\n"
        "kind: shape\n"
        f"pending_since_turn: {turn_when_pending}\n"
        "outcome: pending\n"
        "---\n"
    )
    return {
        "id": f"note-{turn_when_pending}",
        "user_id": "u1",
        "tags": json.dumps([
            "kind/shape", "owner/agent", "topic/n8n", "outcome/pending"
        ]),
        "title_key": f"shape-{turn_when_pending}",
        "content": fm + "Body",
        "updated_at": "2026-04-29 10:00:00",
    }


@pytest.fixture(autouse=True)
def _reset_turn_counter():
    tc.reset_for_test()
    yield
    tc.reset_for_test()


# ── Auto-promote (run_verification_pump) ────────────────────────────


def _decoded_get(notes):
    """Mirror production ``lb_store.get_note``: tags JSON → list[str]."""
    async def fake_get_note(config, user_id, note_id):
        match = next((n for n in notes if n["id"] == note_id), None)
        if not match:
            return None
        out = dict(match)
        out["tags"] = json.loads(out.get("tags") or "[]")
        return out
    return fake_get_note


def test_pump_promotes_after_quiet_window(monkeypatch):
    notes = [_seed_pending(1)]
    fake_store_calls: list[Any] = []

    fake_get_note = _decoded_get(notes)

    async def fake_update_note(config, user_id, note_id, **kw):
        fake_store_calls.append((note_id, kw))
        # Mutate our in-memory note so subsequent reads see the new state.
        match = next((n for n in notes if n["id"] == note_id), None)
        if match and "tags" in kw:
            match["tags"] = json.dumps(kw["tags"])
        return match

    monkeypatch.setattr("lazyclaw.lazybrain.store.get_note", fake_get_note)
    monkeypatch.setattr("lazyclaw.lazybrain.store.update_note", fake_update_note)
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )
    # Advance the user past the quiet window.
    for _ in range(tc.QUIET_TURNS_FOR_VERIFY + 2):
        tc.advance_turn("u1")

    promoted = _run(ver.run_verification_pump(config=None, user_id="u1"))
    assert promoted == 1
    assert len(fake_store_calls) == 1
    note_id, kw = fake_store_calls[0]
    assert note_id == "note-1"
    assert "outcome/verified" in kw["tags"]
    assert kw["frontmatter_updates"]["outcome"] == OUTCOME_VERIFIED
    assert kw["frontmatter_updates"]["pending_since_turn"] is None


def test_pump_does_not_promote_inside_quiet_window(monkeypatch):
    notes = [_seed_pending(2)]
    calls: list = []

    fake_get_note = _decoded_get(notes)

    async def fake_update_note(config, user_id, note_id, **kw):
        calls.append((note_id, kw))
        return None

    monkeypatch.setattr("lazyclaw.lazybrain.store.get_note", fake_get_note)
    monkeypatch.setattr("lazyclaw.lazybrain.store.update_note", fake_update_note)
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )

    # Only one turn elapsed since pending_since_turn=2 → not enough.
    tc.advance_turn("u1")
    tc.advance_turn("u1")
    tc.advance_turn("u1")
    # Now turn=3, since=2, diff=1 < 3
    promoted = _run(ver.run_verification_pump(config=None, user_id="u1"))
    assert promoted == 0
    assert calls == []


def test_pump_skips_when_no_pending_notes(monkeypatch):
    notes = []
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )
    monkeypatch.setattr(
        "lazyclaw.lazybrain.store.get_note",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    for _ in range(5):
        tc.advance_turn("u1")
    promoted = _run(ver.run_verification_pump(config=None, user_id="u1"))
    assert promoted == 0


# ── Manual /confirm /reject ─────────────────────────────────────────


def test_confirm_latest_promotes_pending_to_verified(monkeypatch):
    notes = [_seed_pending(1)]
    captured: list = []

    async def fake_update_note(config, user_id, note_id, **kw):
        captured.append((note_id, kw))
        return None

    monkeypatch.setattr("lazyclaw.lazybrain.store.update_note", fake_update_note)
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )

    out = _run(ver.confirm_latest_shape(config=None, user_id="u1"))
    assert out == "note-1"
    assert "outcome/verified" in captured[0][1]["tags"]
    assert captured[0][1]["frontmatter_updates"]["outcome"] == OUTCOME_VERIFIED


def test_reject_latest_flips_to_known_bad(monkeypatch):
    notes = [_seed_pending(1)]
    captured: list = []

    async def fake_update_note(config, user_id, note_id, **kw):
        captured.append((note_id, kw))
        return None

    monkeypatch.setattr("lazyclaw.lazybrain.store.update_note", fake_update_note)
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )

    out = _run(ver.reject_latest_shape(config=None, user_id="u1"))
    assert out == "note-1"
    new_tags = captured[0][1]["tags"]
    assert "outcome/known-bad" in new_tags
    assert "outcome/pending" not in new_tags


# ── Demote on failure ───────────────────────────────────────────────


def test_demote_on_failure_flips_verified_back_to_pending(monkeypatch):
    notes = [
        {
            "id": "n42",
            "user_id": "u1",
            "title_key": "skill-shape-n8n-create-workflow-create-sheet",
            "tags": json.dumps([
                "kind/shape", "owner/agent", "topic/n8n", "outcome/verified"
            ]),
            "content": "---\nkind: shape\noutcome: verified\n---\nBody",
            "updated_at": "2026-04-29 10:00:00",
        },
    ]
    captured: list = []

    async def fake_update_note(config, user_id, note_id, **kw):
        captured.append((note_id, kw))
        return None

    monkeypatch.setattr("lazyclaw.lazybrain.store.update_note", fake_update_note)
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )

    tc.advance_turn("u1")
    tc.advance_turn("u1")
    flipped = _run(ver.demote_on_failure(
        config=None, user_id="u1",
        title_key="skill-shape-n8n-create-workflow-create-sheet",
    ))
    assert flipped is True
    assert captured[0][0] == "n42"
    assert "outcome/pending" in captured[0][1]["tags"]
    assert captured[0][1]["frontmatter_updates"]["outcome"] == OUTCOME_PENDING
    assert captured[0][1]["frontmatter_updates"]["pending_since_turn"] == 2


def test_demote_on_failure_no_op_when_no_verified_card(monkeypatch):
    notes: list = []
    monkeypatch.setattr(
        "lazyclaw.db.connection.db_session", lambda cfg: _DBContext(_FakeDB(notes))
    )
    monkeypatch.setattr(
        "lazyclaw.lazybrain.store.update_note",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    flipped = _run(ver.demote_on_failure(
        config=None, user_id="u1", title_key="nope",
    ))
    assert flipped is False
