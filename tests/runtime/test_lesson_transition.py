"""End-to-end coverage for ``runtime.skill_lesson.transition_outcome``.

The transition function is what /confirm and /reject Telegram commands
call. Bugs here are user-visible — the lesson stays stuck in pending,
or transitions to the wrong state, or the journal trail goes missing.

Tests use the same DB fixture pattern as test_lazybrain_substrate_db.py
so the encryption + DEK derivation paths run for real.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store
from lazyclaw.runtime import skill_lesson


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-lt", "alice", "x", "salt-lesson-transition"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _seed_pending_lesson(cfg: Config) -> str:
    """Drop a pending kind/shape card directly via save_skill_lesson."""
    note_id = await skill_lesson.save_skill_lesson(
        cfg, "u-lt",
        topic="browser",
        action="open",
        intent="open booking page on appointment site",
        params={"url": "https://example.com/book"},
        outcome=skill_lesson.OUTCOME_PENDING,
    )
    assert note_id is not None
    return note_id


@pytest.mark.asyncio
async def test_pending_to_verified_promotes_and_journals(cfg: Config) -> None:
    lesson_id = await _seed_pending_lesson(cfg)

    result = await skill_lesson.transition_outcome(
        cfg, "u-lt",
        lesson_id=lesson_id,
        target=skill_lesson.OUTCOME_VERIFIED,
        reason="Manual confirmation after re-run",
    )
    assert result["ok"] is True
    assert result["from"] == "pending"
    assert result["to"] == "verified"

    # Verify the underlying note's tags + frontmatter both moved.
    note = await store.get_note(cfg, "u-lt", lesson_id)
    assert note is not None
    assert "outcome/verified" in note["tags"]
    assert "outcome/pending" not in note["tags"]
    # Frontmatter outcome too — that's what recall_skill_lessons reads.
    assert "outcome: verified" in note["content"]


@pytest.mark.asyncio
async def test_pending_to_known_bad_records_reason(cfg: Config) -> None:
    lesson_id = await _seed_pending_lesson(cfg)

    result = await skill_lesson.transition_outcome(
        cfg, "u-lt",
        lesson_id=lesson_id,
        target=skill_lesson.OUTCOME_KNOWN_BAD,
        reason="Broken selector after 2026 site redesign",
    )
    assert result["ok"] is True
    assert result["to"] == "known-bad"

    note = await store.get_note(cfg, "u-lt", lesson_id)
    assert "outcome/known-bad" in note["tags"]
    assert "broken selector" in note["content"].lower(), (
        "transition_reason must land in the note's frontmatter"
    )


@pytest.mark.asyncio
async def test_invalid_target_state_rejected(cfg: Config) -> None:
    lesson_id = await _seed_pending_lesson(cfg)

    # Only verified / known-bad are user-driveable transitions. Failed
    # / superseded come from the system, never from /confirm or /reject.
    result = await skill_lesson.transition_outcome(
        cfg, "u-lt",
        lesson_id=lesson_id, target=skill_lesson.OUTCOME_FAILED,
    )
    assert result["ok"] is False
    note = await store.get_note(cfg, "u-lt", lesson_id)
    assert "outcome/pending" in note["tags"], "state must not change on rejection"


@pytest.mark.asyncio
async def test_transition_writes_typed_journal_edge(cfg: Config) -> None:
    """A successful transition writes a `references` typed edge from
    today's journal note to the lesson, with source=lesson_<target>.
    Without this the typed-edge graph wouldn't see the verification trail."""
    lesson_id = await _seed_pending_lesson(cfg)

    await skill_lesson.transition_outcome(
        cfg, "u-lt",
        lesson_id=lesson_id, target=skill_lesson.OUTCOME_VERIFIED,
    )

    edges_in = await store.list_relations(
        cfg, "u-lt", lesson_id, direction="in", kind="references",
    )
    sources = [e["source"] for e in edges_in]
    assert any(s == "lesson_verified" for s in sources), (
        f"expected at least one lesson_verified edge, got sources={sources}"
    )


@pytest.mark.asyncio
async def test_transition_on_missing_lesson_id_fails_softly(cfg: Config) -> None:
    result = await skill_lesson.transition_outcome(
        cfg, "u-lt",
        lesson_id="does-not-exist",
        target=skill_lesson.OUTCOME_VERIFIED,
    )
    assert result["ok"] is False
    assert "not found" in (result.get("reason") or "")


@pytest.mark.asyncio
async def test_transition_refuses_non_shape_notes(cfg: Config) -> None:
    """transition_outcome only operates on kind/shape cards. A regular
    user note must be left alone — the command is for the lesson loop,
    not generic note editing."""
    plain = await store.save_note(
        cfg, "u-lt",
        content="Just a regular memo, not a lesson.",
        title="Memo",
        tags=["memory", "owner/user"],
    )
    result = await skill_lesson.transition_outcome(
        cfg, "u-lt",
        lesson_id=plain["id"],
        target=skill_lesson.OUTCOME_VERIFIED,
    )
    assert result["ok"] is False
    assert "skill-shape" in (result.get("reason") or "").lower()
