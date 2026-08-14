"""``list_memory_notes`` — the dedicated auto-inject candidate query.

Defect (2026-08-14 audit, known-latent): ``runtime/context_builder.py``
fed its memory pool from ``list_notes(limit=40)``, which is
``ORDER BY pinned DESC, created_at DESC``. The note store is ~93%
auto-captured noise (per-message captures, per-tool lesson cards,
per-URL visit notes), so the 40-newest window churned within HOURS and
durable user/project facts became permanently invisible to the cached
system prompt.

``list_memory_notes`` selects candidates directly instead:
``memory_type IN (AUTO_INJECT_TYPES)`` (plaintext indexed column) +
the shared tag-exclusion families, ordered by ``importance DESC,
created_at DESC``.

The load-bearing test is
:func:`test_durable_fact_survives_100_newer_auto_notes`, which asserts
BOTH sides: the durable note is returned by the new query AND is absent
from the old ``list_notes(limit=40)`` window (so the test can't pass
trivially if someone reverts the call site).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store as note_store
from lazyclaw.lazybrain.store import _content_aad, _title_aad

pytestmark = pytest.mark.asyncio

_USER_ID = "u-memnotes"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "memnotes", "x", "salt-memnotes-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _insert_raw_note(
    cfg: Config,
    note_id: str,
    *,
    content: str,
    title: str = "note",
    tags_json: str | None = None,
    importance: int = 5,
    memory_type: str | None = "fact",
    created_at: str = "2026-08-14 12:00:00.000000",
    archived: int = 0,
    deleted_at: str | None = None,
) -> None:
    """Insert a note straight into SQL.

    Direct INSERT (not ``save_note``) so the test controls ``created_at``,
    ``memory_type`` and ``tags`` exactly — and so bulk noise rows don't
    each fire the post-save embedding/wikilink hooks.
    """
    dek = await get_user_dek(cfg, _USER_ID)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, importance, "
            "pinned, title_key, memory_type, archived, embedding_dirty, "
            "deleted_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, ?, ?, ?)",
            (
                note_id,
                _USER_ID,
                encrypt_field(title, dek, _title_aad(_USER_ID)),
                encrypt_field(content, dek, _content_aad(_USER_ID)),
                tags_json,
                importance,
                title.lower(),
                memory_type,
                archived,
                deleted_at,
                created_at,
                created_at,
            ),
        )
        await db.commit()


# ─── The churn defect ─────────────────────────────────────────────────────


async def test_durable_fact_survives_100_newer_auto_notes(cfg):
    """A durable typed fact is still a candidate under 120 newer auto-notes.

    Also asserts the OLD path (``list_notes(limit=40)``) misses it — that
    negative is what makes this a regression test and not a tautology.
    """
    await _insert_raw_note(
        cfg,
        "note-durable",
        content="User's Google Workspace email is blckitteam@gmail.com",
        title="User email",
        tags_json='["user"]',
        importance=9,
        memory_type="user",
        created_at="2026-01-01 09:00:00.000000",  # OLD
    )
    # 120 newer auto-capture notes — auto-inject-typed too, so the only
    # thing separating them from the durable fact is importance + age.
    for i in range(120):
        await _insert_raw_note(
            cfg,
            f"note-noise-{i:03d}",
            content=f"auto-captured chatter {i}",
            title=f"chatter {i}",
            tags_json='["auto"]',
            importance=5,
            memory_type="fact",
            created_at=f"2026-08-14 10:{i // 60:02d}:{i % 60:02d}.000000",
        )

    candidates = await note_store.list_memory_notes(cfg, _USER_ID, limit=40)
    ids = [n["id"] for n in candidates]
    assert "note-durable" in ids, (
        "durable user fact aged out of the auto-inject candidate window"
    )
    # Importance ordering puts it first, not merely "somewhere in 40".
    assert ids[0] == "note-durable"

    # Negative control: the generic newest-first listing DOES lose it.
    newest = await note_store.list_notes(cfg, _USER_ID, limit=40)
    assert "note-durable" not in [n["id"] for n in newest], (
        "list_notes unexpectedly returned the old note — the churn "
        "scenario this test guards is no longer reproduced"
    )


async def test_ordering_is_importance_then_recency(cfg):
    await _insert_raw_note(
        cfg, "n-low-new", content="low but new", importance=3,
        memory_type="user", created_at="2026-08-14 23:00:00.000000",
    )
    await _insert_raw_note(
        cfg, "n-high-old", content="high but old", importance=9,
        memory_type="user", created_at="2026-01-01 01:00:00.000000",
    )
    await _insert_raw_note(
        cfg, "n-high-new", content="high and new", importance=9,
        memory_type="user", created_at="2026-08-14 22:00:00.000000",
    )

    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == ["n-high-new", "n-high-old", "n-low-new"]


# ─── Type + tag gating ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "memory_type, expected",
    [
        ("user", True),
        ("feedback", True),
        ("project", True),
        ("reference", True),
        ("fact", True),
        ("session-log", False),
        ("other", False),
        (None, False),  # pre-backfill rows fail closed
    ],
)
async def test_memory_type_gate(cfg, memory_type, expected):
    await _insert_raw_note(
        cfg, "n-typed", content="typed content", memory_type=memory_type,
    )
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ("n-typed" in ids) is expected


@pytest.mark.parametrize(
    "tags_json",
    [
        '["daily-log"]',
        '["session-end"]',
        '["kind/shape", "outcome/verified"]',
        '["kind/legacy"]',
        '["rolled-up"]',
    ],
)
async def test_noise_tag_families_excluded_in_sql(cfg, tags_json):
    """These families are dropped in SQL so they never eat candidate slots.

    ``kind/shape`` lesson cards are the load-bearing case: they classify as
    ``fact`` (auto-inject) and are minted per tool run, so without the SQL
    exclusion they'd refill the window and be discarded by the caller's
    post-filter — churn all over again.
    """
    await _insert_raw_note(
        cfg, "n-noise", content="noise", tags_json=tags_json, memory_type="fact",
    )
    await _insert_raw_note(
        cfg, "n-real", content="real fact", tags_json='["user"]',
        memory_type="user",
    )
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == ["n-real"]


async def test_untagged_notes_are_not_dropped_by_the_not_like_guard(cfg):
    """SQLite ``NULL NOT LIKE 'x'`` is NULL → falsy in WHERE. Without the
    ``tags IS NULL OR`` guard every tag-less note would vanish."""
    await _insert_raw_note(
        cfg, "n-untagged", content="tagless durable fact", tags_json=None,
        memory_type="user",
    )
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == ["n-untagged"]


async def test_archived_and_deleted_are_excluded(cfg):
    await _insert_raw_note(
        cfg, "n-archived", content="archived", memory_type="user", archived=1,
    )
    await _insert_raw_note(
        cfg, "n-deleted", content="deleted", memory_type="user",
        deleted_at="2026-08-14 13:00:00.000000",
    )
    await _insert_raw_note(cfg, "n-live", content="live", memory_type="user")
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == ["n-live"]


async def test_user_isolation(cfg):
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-other", "other", "x", "salt-other"),
        )
        await db.commit()
    await _insert_raw_note(cfg, "n-mine", content="mine", memory_type="user")
    assert await note_store.list_memory_notes(cfg, "u-other") == []


# ─── The MEMORY_UNIFIED mirror gate ───────────────────────────────────────


async def test_memory_mirror_excluded_in_dual_write_mode(cfg):
    """Flag OFF: the ``#memory`` mirror is a duplicate of the legacy
    personal_memory row already in the pool — keep it out (no double-hit)."""
    await _insert_raw_note(
        cfg, "n-mirror", content="mirrored fact",
        tags_json='["memory", "auto", "owner/user", "kind/fact"]',
        memory_type="fact",
    )
    cfg.memory_unified = False
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == []


async def test_memory_mirror_included_in_unified_mode(cfg):
    """Flag ON: ``save_memory`` no longer writes the legacy row, so the
    mirror IS the fact — it must be a candidate."""
    await _insert_raw_note(
        cfg, "n-mirror", content="mirrored fact",
        tags_json='["memory", "auto", "owner/user", "kind/fact"]',
        memory_type="fact",
    )
    cfg.memory_unified = True
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == ["n-mirror"]


async def test_unified_mode_still_excludes_the_other_noise_families(cfg):
    """Flipping MEMORY_UNIFIED must relax ONLY the ``#memory`` tag."""
    cfg.memory_unified = True
    await _insert_raw_note(
        cfg, "n-daily", content="daily", tags_json='["daily-log"]',
        memory_type="fact",
    )
    await _insert_raw_note(
        cfg, "n-shape", content="shape", tags_json='["kind/shape"]',
        memory_type="fact",
    )
    await _insert_raw_note(
        cfg, "n-mirror", content="mirror", tags_json='["memory"]',
        memory_type="user",
    )
    ids = [n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)]
    assert ids == ["n-mirror"]


async def test_explicit_include_memory_mirrors_overrides_config(cfg):
    await _insert_raw_note(
        cfg, "n-mirror", content="mirror", tags_json='["memory"]',
        memory_type="user",
    )
    cfg.memory_unified = False
    forced = await note_store.list_memory_notes(
        cfg, _USER_ID, include_memory_mirrors=True,
    )
    assert [n["id"] for n in forced] == ["n-mirror"]

    cfg.memory_unified = True
    suppressed = await note_store.list_memory_notes(
        cfg, _USER_ID, include_memory_mirrors=False,
    )
    assert suppressed == []


async def test_returned_rows_are_decrypted_and_shaped_like_list_notes(cfg):
    note = await note_store.save_note(
        cfg, _USER_ID, content="decrypt me", title="Shape check",
        tags=["user"], importance=7,
    )
    rows = await note_store.list_memory_notes(cfg, _USER_ID)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == note["id"]
    assert row["title"] == "Shape check"
    assert row["content"].startswith("decrypt me")
    assert row["memory_type"] == "user"
    assert row["importance"] == 7
    assert row["pinned"] is False
    # Same key set as list_notes so callers can swap the query in place.
    listed = await note_store.list_notes(cfg, _USER_ID)
    assert set(row) == set(listed[0])
