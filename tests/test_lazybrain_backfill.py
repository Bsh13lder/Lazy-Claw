"""DB-backed tests for the typed-memory backfill migration.

Verifies that pre-migration notes (``memory_type IS NULL`` rows that
existed before commit 3de968c shipped the column) get classified on
startup and that re-running the backfill is a no-op.

Without this pass, every existing note stays NULL → ``is_auto_inject_type
(None)`` fails closed → the brain loses access to typed memory until a
write touches the row. The backfill turns the lights back on by labelling
in bulk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import migrations as migrations_mod
from lazyclaw.lazybrain.store import _content_aad, _title_aad


_USER_ID = "u-backfill"


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "backfill", "x", "salt-backfill-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


async def _raw_insert(
    cfg: Config,
    note_id: str,
    *,
    title: str,
    content: str,
    tags_json: str | None,
) -> None:
    """Write a notes row with ``memory_type=NULL``, mirroring a pre-3de968c
    write that landed before the column existed.

    We can't use ``save_note`` because Phase 1 now auto-classifies on
    write — the test needs the column to land as NULL so the backfill
    has something to find.
    """
    dek = await get_user_dek(cfg, _USER_ID)
    enc_title = encrypt_field(title, dek, _title_aad(_USER_ID))
    enc_content = encrypt_field(content, dek, _content_aad(_USER_ID))
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, "
            "importance, pinned, title_key, memory_type, "
            "embedding_dirty, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, "
            "datetime('now'), datetime('now'))",
            (
                note_id, _USER_ID, enc_title, enc_content, tags_json,
                5, 0, title.lower(),
            ),
        )
        await db.commit()


async def _fetch_memory_type(cfg: Config, note_id: str) -> str | None:
    async with db_session(cfg) as db:
        rows = await db.execute(
            "SELECT memory_type FROM notes WHERE id = ?", (note_id,),
        )
        row = await rows.fetchone()
    return row[0] if row else None


# ─── Core classification ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_classifies_each_canonical_scope(
    tmp_config: Config,
) -> None:
    """Four pre-migration rows, one per scope the classifier handles via
    tag dispatch. Each must land on the expected memory_type."""
    await _raw_insert(
        tmp_config, "note-fb",
        title="user prefers no emoji in commits",
        content="rule: never add emoji to commit messages.",
        tags_json='["feedback"]',
    )
    await _raw_insert(
        tmp_config, "note-proj",
        title="F1 phase 2 plan",
        content="phase 2 — wire memory_type filter in context_builder.py.",
        tags_json='["project", "plan"]',
    )
    await _raw_insert(
        tmp_config, "note-ref",
        title="claude citations docs",
        content="https://platform.claude.com/docs/en/build-with-claude/citations",
        tags_json='["reference", "url"]',
    )
    await _raw_insert(
        tmp_config, "note-sess",
        title="upwork recap 2026-05-19",
        content="## Last Upwork Conversation\n**Contact:** James",
        tags_json='["channel-thread"]',
    )

    summary = await migrations_mod.backfill_memory_types(
        tmp_config, _USER_ID,
    )

    assert summary["scanned"] == 4
    assert summary["updated"] == 4
    assert summary["errors"] == 0
    assert summary["skipped"] == 0

    assert await _fetch_memory_type(tmp_config, "note-fb") == "feedback"
    assert await _fetch_memory_type(tmp_config, "note-proj") == "project"
    assert await _fetch_memory_type(tmp_config, "note-ref") == "reference"
    assert await _fetch_memory_type(tmp_config, "note-sess") == "session-log"


@pytest.mark.asyncio
async def test_backfill_falls_back_to_fact_for_unclassified(
    tmp_config: Config,
) -> None:
    """A note with no tag signal and a neutral first line falls back to
    the default ``fact`` scope — still useful, still queryable, just
    not auto-injected."""
    await _raw_insert(
        tmp_config, "note-neutral",
        title="random observation",
        content="the sky was overcast today around midday.",
        tags_json=None,
    )

    summary = await migrations_mod.backfill_memory_types(
        tmp_config, _USER_ID,
    )

    assert summary["scanned"] == 1
    assert summary["updated"] == 1
    assert await _fetch_memory_type(tmp_config, "note-neutral") == "fact"


# ─── Idempotency + WHERE-narrowing ────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_is_idempotent_on_repeated_runs(
    tmp_config: Config,
) -> None:
    """First run classifies; second run scans zero rows because the
    SELECT narrows to ``memory_type IS NULL``."""
    await _raw_insert(
        tmp_config, "note-one",
        title="rule: stop summarizing", content="stop summarizing.",
        tags_json='["feedback"]',
    )

    first = await migrations_mod.backfill_memory_types(tmp_config, _USER_ID)
    assert first["updated"] == 1
    assert first["scanned"] == 1

    second = await migrations_mod.backfill_memory_types(tmp_config, _USER_ID)
    assert second["scanned"] == 0
    assert second["updated"] == 0


@pytest.mark.asyncio
async def test_backfill_does_not_touch_already_classified_rows(
    tmp_config: Config,
) -> None:
    """A row whose ``memory_type`` was already set (e.g. by a fresh
    save_note write post-3de968c) must NOT be revisited — the SELECT
    filter on ``IS NULL`` is the contract."""
    dek = await get_user_dek(tmp_config, _USER_ID)
    enc_title = encrypt_field("preserved", dek, _title_aad(_USER_ID))
    enc_content = encrypt_field("body", dek, _content_aad(_USER_ID))
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, "
            "importance, pinned, title_key, memory_type, "
            "embedding_dirty, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, "
            "datetime('now'), datetime('now'))",
            (
                "note-preserved", _USER_ID, enc_title, enc_content, None,
                5, 0, "preserved", "reference",
            ),
        )
        await db.commit()

    summary = await migrations_mod.backfill_memory_types(
        tmp_config, _USER_ID,
    )

    assert summary["scanned"] == 0  # Already-classified row not in SELECT
    assert await _fetch_memory_type(tmp_config, "note-preserved") == "reference"


# ─── Cross-user iteration ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_all_users_iterates_every_user(
    tmp_config: Config,
) -> None:
    """The startup pass walks every row in ``users`` so a multi-user
    install gets every account classified in one sweep."""
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-second", "second", "x", "salt-second-user-test"),
        )
        await db.commit()

    await _raw_insert(
        tmp_config, "note-a",
        title="rule: speak Georgian", content="rule: respond in Georgian.",
        tags_json='["feedback"]',
    )

    # Insert a row owned by the second user — same NULL memory_type shape.
    dek2 = await get_user_dek(tmp_config, "u-second")
    enc_title = encrypt_field("plan", dek2, _title_aad("u-second"))
    enc_content = encrypt_field("plan: ship phase 2", dek2, _content_aad("u-second"))
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, "
            "importance, pinned, title_key, memory_type, "
            "embedding_dirty, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, "
            "datetime('now'), datetime('now'))",
            (
                "note-b", "u-second", enc_title, enc_content, '["plan"]',
                5, 0, "plan",
            ),
        )
        await db.commit()

    summaries = await migrations_mod.backfill_memory_types_all_users(
        tmp_config,
    )

    # Two users → two summary dicts.
    assert len(summaries) == 2
    by_user = {s.get("user_id"): s for s in summaries if "user_id" in s}
    assert by_user[_USER_ID]["updated"] == 1
    assert by_user["u-second"]["updated"] == 1

    assert await _fetch_memory_type(tmp_config, "note-a") == "feedback"
    assert await _fetch_memory_type(tmp_config, "note-b") == "project"
