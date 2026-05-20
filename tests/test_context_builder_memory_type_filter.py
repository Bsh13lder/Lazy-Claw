"""Verifies the typed-memory auto-inject filter in context_builder.py.

The filter is the load-bearing line: it decides which LazyBrain notes
get pre-injected into the system prompt every turn versus which stay
queryable on-demand only. Misclassification here re-opens the 2026-05-19
contamination class.

The pure helper (``is_auto_inject_type``) is covered exhaustively by
``tests/test_lazybrain_memory_types.py``. These tests verify the
*wiring* — that the merge loop in ``context_builder.py`` actually calls
that helper and respects its verdict against a live DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store as lb_store


_USER_ID = "u-cb-filter"


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "cb-filter", "x", "salt-cb-filter-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# ─── list_notes surfaces memory_type ──────────────────────────────────────


@pytest.mark.asyncio
async def test_list_notes_returns_memory_type_field(
    tmp_config: Config,
) -> None:
    """The SELECT path must expose memory_type so the filter can see it.
    A regression that drops the column from the SELECT silently
    re-opens contamination (filter reads None → fails closed → all
    notes drop out)."""
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="rule: never use emoji in commits.",
        title="emoji rule",
        tags=["feedback"],
    )
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="random fact about ocean salinity.",
        title="ocean fact",
    )

    notes = await lb_store.list_notes(tmp_config, _USER_ID, limit=10)
    assert len(notes) == 2

    by_title = {n["title"]: n for n in notes}
    assert by_title["emoji rule"]["memory_type"] == "feedback"
    assert by_title["ocean fact"]["memory_type"] == "fact"


# ─── End-to-end filter behaviour ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_keeps_only_auto_inject_types(
    tmp_config: Config,
) -> None:
    """Five notes — one per scope. After running list_notes + the same
    filter loop the context builder uses, only ``user | feedback |
    project | reference`` survive. The 2026-05-19 incident pattern
    (session-log) must be excluded."""
    from lazyclaw.lazybrain.memory_types import is_auto_inject_type

    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="user prefers Sonnet over Opus for routine chat.",
        title="model preference", tags=["user"],
    )
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="rule: don't add emoji.",
        title="commit rule", tags=["feedback"],
    )
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="plan: ship F1 grounding by Friday.",
        title="F1 plan", tags=["project"],
    )
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="https://platform.claude.com/docs/citations",
        title="claude citations docs", tags=["reference", "url"],
    )
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content="## Last Upwork Conversation\n**Contact:** James Blue",
        title="upwork 2026-05-19 recap", tags=["channel-thread"],
    )

    notes = await lb_store.list_notes(tmp_config, _USER_ID, limit=20)
    assert len(notes) == 5

    survivors = [n for n in notes if is_auto_inject_type(n.get("memory_type"))]
    survivor_types = {n["memory_type"] for n in survivors}

    assert survivor_types == {"user", "feedback", "project", "reference"}
    # The session-log paraphrase is the explicit one we want excluded —
    # it's the load-bearing assertion: this row WAS in the SELECT, but
    # the filter dropped it before it could enter the pool.
    excluded_titles = {
        n["title"] for n in notes if n not in survivors
    }
    assert "upwork 2026-05-19 recap" in excluded_titles


@pytest.mark.asyncio
async def test_filter_excludes_null_memory_type_rows(
    tmp_config: Config,
) -> None:
    """A note that landed before Phase 1 (memory_type IS NULL) must NOT
    auto-inject. The fail-closed semantics in is_auto_inject_type(None)
    are the safety net while the backfill catches up."""
    from lazyclaw.crypto.encryption import encrypt_field
    from lazyclaw.crypto.key_manager import get_user_dek
    from lazyclaw.lazybrain.memory_types import is_auto_inject_type
    from lazyclaw.lazybrain.store import _content_aad, _title_aad

    dek = await get_user_dek(tmp_config, _USER_ID)
    enc_title = encrypt_field("legacy row", dek, _title_aad(_USER_ID))
    enc_content = encrypt_field(
        "pre-phase-1 content", dek, _content_aad(_USER_ID),
    )
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, "
            "importance, pinned, title_key, memory_type, "
            "embedding_dirty, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, "
            "datetime('now'), datetime('now'))",
            (
                "note-legacy", _USER_ID, enc_title, enc_content, None,
                5, 0, "legacy row",
            ),
        )
        await db.commit()

    notes = await lb_store.list_notes(tmp_config, _USER_ID, limit=10)
    assert len(notes) == 1
    assert notes[0]["memory_type"] is None

    survivors = [n for n in notes if is_auto_inject_type(n.get("memory_type"))]
    assert survivors == []
