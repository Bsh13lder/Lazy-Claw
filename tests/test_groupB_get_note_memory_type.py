"""Tests for B2 — get_note must return the memory_type column.

Before the fix, store.get_note omitted memory_type from its SELECT, so
the returned dict had no 'memory_type' key. Downstream callers —
paraphrase_sanitizer.is_paraphrase_class(note.get("memory_type")) —
received None and failed closed, wrapping authoritative notes in
"[CACHED PARAPHRASE]" framing unnecessarily.

These integration tests:
  1. Verify that get_note returns memory_type == "user" when the note
     was saved with memory_type="user".
  2. Verify that when no explicit memory_type is passed to save_note
     (and the classifier defaults to "fact"), get_note returns
     memory_type == "fact".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store

pytestmark = pytest.mark.asyncio

_USER_ID = "u-b2-store"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "b2user", "x", "salt-b2-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# B2.1 — explicit memory_type="user" round-trips through get_note
# ---------------------------------------------------------------------------

async def test_get_note_returns_explicit_memory_type(cfg):
    """save_note with memory_type='user' → get_note returns memory_type == 'user'."""
    note = await store.save_note(
        cfg,
        _USER_ID,
        content="This is a personal preference note.",
        title="My preference",
        memory_type="user",
    )
    note_id = note["id"]

    fetched = await store.get_note(cfg, _USER_ID, note_id)

    assert fetched is not None, "get_note must find the newly saved note"
    assert "memory_type" in fetched, (
        "get_note must include 'memory_type' in returned dict"
    )
    assert fetched["memory_type"] == "user", (
        f"expected memory_type='user', got {fetched['memory_type']!r}"
    )


# ---------------------------------------------------------------------------
# B2.2 — no explicit memory_type → classifier picks 'fact' → get_note exposes it
# ---------------------------------------------------------------------------

async def test_get_note_returns_classifier_default_fact(cfg):
    """save_note without memory_type → classifier defaults to 'fact' → get_note returns 'fact'."""
    # A note with generic content and no tags that map to a special type
    # should be classified as 'fact' by the deterministic classifier.
    note = await store.save_note(
        cfg,
        _USER_ID,
        content="Python 3.12 ships with per-interpreter GIL.",
        title="Python GIL note",
        tags=["trivia"],          # 'trivia' is not in any classifier bucket
    )
    note_id = note["id"]

    # Sanity-check save_note's own return value first (so we know what was stored)
    assert "memory_type" in note, "save_note should also return memory_type"
    assert note["memory_type"] == "fact", (
        f"expected save_note to classify as 'fact', got {note['memory_type']!r}"
    )

    fetched = await store.get_note(cfg, _USER_ID, note_id)

    assert fetched is not None
    assert "memory_type" in fetched, (
        "get_note must include 'memory_type' in returned dict"
    )
    assert fetched["memory_type"] == "fact", (
        f"expected get_note to return memory_type='fact', got {fetched['memory_type']!r}"
    )


# ---------------------------------------------------------------------------
# B2.3 — None is not returned for memory_type (would break is_paraphrase_class)
# ---------------------------------------------------------------------------

async def test_get_note_memory_type_is_never_absent(cfg):
    """get_note dict must always have a 'memory_type' key (not absent/KeyError)."""
    note = await store.save_note(
        cfg,
        _USER_ID,
        content="Some content for completeness check.",
        title="Completeness",
    )
    fetched = await store.get_note(cfg, _USER_ID, note["id"])
    assert fetched is not None
    # Must be present and a string — not None (which would break is_paraphrase_class)
    assert "memory_type" in fetched, "memory_type key must exist in get_note result"
    assert isinstance(fetched["memory_type"], str), (
        f"memory_type must be a str, got {type(fetched['memory_type'])}"
    )


# ---------------------------------------------------------------------------
# B2.4 — get_note returns None for missing note (regression guard)
# ---------------------------------------------------------------------------

async def test_get_note_returns_none_for_missing_note(cfg):
    """get_note returns None for a non-existent note_id (existing contract)."""
    result = await store.get_note(cfg, _USER_ID, "nonexistent-id")
    assert result is None
