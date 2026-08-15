"""``archive_stale_auto_notes`` — the retention sweep that walks auto-capture
noise out of default recall.

Context (2026-08-14 audit): 93.5% of the note store (2,676 of 2,863) is
auto-captured — one note per user message, one per browsed URL, one per task
status transition. ``notes.archived`` was already respected by every default
reader but NO writer ever set it (audit finding #7: a documented no-op).
This suite pins the writer.

The load-bearing shape is the **rule matrix**: each protective condition is
tested in isolation against an otherwise-archivable note, so a future edit
that drops one guard (e.g. forgets ``pinned``) fails a specific test instead
of silently widening the blast radius onto durable memories.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store as note_store
from lazyclaw.lazybrain.maintenance import archive_stale_auto_notes
from lazyclaw.lazybrain.store import _content_aad, _title_aad

pytestmark = pytest.mark.asyncio

_USER_ID = "u-archive"
_OTHER_USER_ID = "u-archive-other"

_NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    """Timestamp ``days_ago`` before the pinned test ``now``, store format."""
    return (_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S.%f")


# Well clear of both cutoffs (30d auto / 14d visit).
OLD = _ts(60)
# Older than the 14d visit cutoff but younger than the 30d auto cutoff.
MID = _ts(20)
# Younger than everything.
YOUNG = _ts(1)


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        for uid, uname in ((_USER_ID, "archive"), (_OTHER_USER_ID, "archive2")):
            await db.execute(
                "INSERT INTO users (id, username, password_hash, encryption_salt) "
                "VALUES (?, ?, ?, ?)",
                (uid, uname, "x", f"salt-{uname}-test"),
            )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _insert_note(
    cfg: Config,
    note_id: str,
    *,
    user_id: str = _USER_ID,
    tags_json: str | None = '["auto"]',
    importance: int | None = 5,
    pinned: int = 0,
    memory_type: str | None = "session-log",
    created_at: str = OLD,
    archived: int = 0,
    deleted_at: str | None = None,
) -> None:
    """Insert a note straight into SQL.

    Direct INSERT (not ``save_note``) so the test owns ``created_at``,
    ``memory_type``, ``archived`` and ``pinned`` exactly — and so noise rows
    don't each fire the post-save embedding / wikilink hooks.

    Defaults describe the CANONICAL ARCHIVABLE NOTE: old, ``#auto``,
    importance 5, unpinned, ``session-log`` (outside AUTO_INJECT_TYPES).
    Every matrix test below flips exactly one field off that baseline.
    """
    dek = await get_user_dek(cfg, user_id)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, importance, "
            "pinned, title_key, memory_type, archived, embedding_dirty, "
            "deleted_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (
                note_id,
                user_id,
                encrypt_field(note_id, dek, _title_aad(user_id)),
                encrypt_field(f"body of {note_id}", dek, _content_aad(user_id)),
                tags_json,
                importance,
                pinned,
                note_id,
                memory_type,
                archived,
                deleted_at,
                created_at,
                created_at,
            ),
        )
        await db.commit()


async def _archived_ids(cfg: Config, user_id: str = _USER_ID) -> set[str]:
    async with db_session(cfg) as db:
        rows = await db.execute(
            "SELECT id FROM notes WHERE user_id = ? AND archived = 1", (user_id,)
        )
        return {r[0] for r in await rows.fetchall()}


async def _sweep(cfg: Config, user_id: str = _USER_ID) -> int:
    return await archive_stale_auto_notes(cfg, user_id, now=_NOW)


# ─── Rule A: stale #auto noise ────────────────────────────────────────────


async def test_stale_auto_noise_is_archived(cfg):
    """The canonical baseline note (old + #auto + imp<=5 + untyped) goes."""
    await _insert_note(cfg, "noise")
    assert await _sweep(cfg) == 1
    assert await _archived_ids(cfg) == {"noise"}


async def test_pinned_note_is_protected(cfg):
    """Pinning is an explicit keep-forever signal — it outranks retention."""
    await _insert_note(cfg, "pinned-note", pinned=1)
    assert await _sweep(cfg) == 0
    assert await _archived_ids(cfg) == set()


async def test_young_note_is_protected(cfg):
    """Under the 30-day cutoff, even pure #auto noise stays live."""
    await _insert_note(cfg, "young", created_at=YOUNG)
    assert await _sweep(cfg) == 0
    assert await _archived_ids(cfg) == set()


async def test_note_just_inside_cutoff_is_protected(cfg):
    """Boundary: 29 days old is NOT yet stale (cutoff is 30)."""
    await _insert_note(cfg, "day29", created_at=_ts(29))
    assert await _sweep(cfg) == 0


async def test_note_just_past_cutoff_is_archived(cfg):
    """Boundary: 31 days old IS stale."""
    await _insert_note(cfg, "day31", created_at=_ts(31))
    assert await _sweep(cfg) == 1


async def test_high_importance_note_is_protected(cfg):
    """importance 6+ is an explicit signal that this is not noise."""
    await _insert_note(cfg, "important", importance=6)
    assert await _sweep(cfg) == 0
    assert await _archived_ids(cfg) == set()


@pytest.mark.parametrize(
    "memory_type", ["user", "feedback", "project", "reference", "fact"]
)
async def test_auto_inject_typed_note_is_protected(cfg, memory_type):
    """Durable typed memories are NEVER archived by the stale-auto rule —
    no matter how old, how low-importance, or how #auto-tagged."""
    await _insert_note(cfg, f"typed-{memory_type}", memory_type=memory_type)
    assert await _sweep(cfg) == 0
    assert await _archived_ids(cfg) == set()


async def test_null_memory_type_is_archivable(cfg):
    """Pre-backfill rows (NULL memory_type) are outside AUTO_INJECT_TYPES.

    ``memory_types.is_auto_inject_type`` already fails closed on None, so
    these are not in the injected pool — archiving them loses nothing. This
    also pins the SQL shape: a bare ``memory_type NOT IN (...)`` would
    evaluate to NULL (→ false) and silently skip every such row.
    """
    await _insert_note(cfg, "untyped", memory_type=None)
    assert await _sweep(cfg) == 1
    assert await _archived_ids(cfg) == {"untyped"}


async def test_non_auto_note_is_protected(cfg):
    """No ``#auto`` tag → not our noise, regardless of age/importance."""
    await _insert_note(cfg, "handwritten", tags_json='["meeting"]')
    assert await _sweep(cfg) == 0
    assert await _archived_ids(cfg) == set()


async def test_untagged_note_is_protected(cfg):
    """A NULL ``tags`` column must not blow up or match the LIKE."""
    await _insert_note(cfg, "no-tags", tags_json=None)
    assert await _sweep(cfg) == 0


async def test_auto_prefixed_tag_does_not_match(cfg):
    """``"auto-generated"`` is a different tag — the quoted LIKE token
    (``%"auto"%``) must not treat it as ``auto``."""
    await _insert_note(cfg, "prefixed", tags_json='["auto-generated"]')
    assert await _sweep(cfg) == 0


async def test_soft_deleted_note_is_skipped(cfg):
    """Tombstones stay tombstones — never re-touched."""
    await _insert_note(cfg, "tombstone", deleted_at="2026-01-01 00:00:00.000000")
    assert await _sweep(cfg) == 0


# ─── Rule B: per-URL browsing breadcrumbs ─────────────────────────────────


@pytest.mark.parametrize("tag", ["visit", "site-memory"])
async def test_visit_note_archived_at_14_days(cfg, tag):
    """Browsing breadcrumbs age out at 14 days, not 30."""
    await _insert_note(
        cfg, f"visit-{tag}",
        tags_json=f'["{tag}", "auto", "owner/agent"]',
        created_at=MID,          # 20d: past the 14d visit cutoff, under 30d
        memory_type="fact",      # visit notes always classify as `fact`
        importance=9,            # "regardless of importance"
    )
    assert await _sweep(cfg) == 1
    assert await _archived_ids(cfg) == {f"visit-{tag}"}


async def test_visit_note_archived_regardless_of_importance(cfg):
    """Explicit: rule B has no importance guard (rule A would block imp=10)."""
    await _insert_note(
        cfg, "hot-visit",
        tags_json='["visit", "site-memory", "auto"]',
        created_at=MID,
        memory_type="fact",
        importance=10,
    )
    assert await _sweep(cfg) == 1


async def test_young_visit_note_is_protected(cfg):
    """Under 14 days, browsing breadcrumbs are still useful context."""
    await _insert_note(
        cfg, "fresh-visit",
        tags_json='["visit", "site-memory", "auto"]',
        created_at=_ts(13),
        memory_type="fact",
    )
    assert await _sweep(cfg) == 0


async def test_pinned_visit_note_is_protected(cfg):
    """The pinned guard applies to rule B too."""
    await _insert_note(
        cfg, "pinned-visit",
        tags_json='["visit", "site-memory", "auto"]',
        created_at=OLD,
        memory_type="fact",
        pinned=1,
    )
    assert await _sweep(cfg) == 0


async def test_visit_note_counted_once_when_both_rules_would_match(cfg):
    """A note matching rule A AND rule B must be counted once, not twice.

    Rule A runs first and flips ``archived = 1``; rule B's
    ``archived = 0`` guard then excludes it. Without that guard the
    returned count would double-report.
    """
    await _insert_note(
        cfg, "double-match",
        tags_json='["visit", "site-memory", "auto"]',
        created_at=OLD,
        memory_type="session-log",   # also satisfies rule A
        importance=3,
    )
    assert await _sweep(cfg) == 1


# ─── Idempotence, isolation, retrieval effect ─────────────────────────────


async def test_second_run_archives_zero(cfg):
    """Idempotent: the ``archived = 0`` guard makes a re-run a no-op."""
    await _insert_note(cfg, "noise-a")
    await _insert_note(cfg, "noise-b")
    await _insert_note(
        cfg, "visit-c", tags_json='["visit", "auto"]',
        created_at=MID, memory_type="fact",
    )
    assert await _sweep(cfg) == 3
    assert await _sweep(cfg) == 0
    assert len(await _archived_ids(cfg)) == 3


async def test_user_isolation(cfg):
    """Sweeping one user must never touch another user's notes."""
    await _insert_note(cfg, "mine", user_id=_USER_ID)
    await _insert_note(cfg, "theirs", user_id=_OTHER_USER_ID)

    assert await _sweep(cfg, _USER_ID) == 1
    assert await _archived_ids(cfg, _USER_ID) == {"mine"}
    assert await _archived_ids(cfg, _OTHER_USER_ID) == set()

    assert await _sweep(cfg, _OTHER_USER_ID) == 1
    assert await _archived_ids(cfg, _OTHER_USER_ID) == {"theirs"}


async def test_archived_notes_leave_default_recall(cfg):
    """The whole point: archived noise disappears from default retrieval but
    is still reachable with ``include_archived=True``."""
    await _insert_note(cfg, "noise")
    await _insert_note(cfg, "keeper", memory_type="user", importance=9)

    before = {n["id"] for n in await note_store.list_notes(cfg, _USER_ID)}
    assert before == {"noise", "keeper"}

    await _sweep(cfg)

    after = {n["id"] for n in await note_store.list_notes(cfg, _USER_ID)}
    assert after == {"keeper"}, "archived note still visible in default recall"

    with_archived = {
        n["id"]
        for n in await note_store.list_notes(cfg, _USER_ID, include_archived=True)
    }
    assert with_archived == {"noise", "keeper"}, "archive must be reversible/visible"


async def test_archived_note_leaves_auto_inject_pool(cfg):
    """``list_memory_notes`` (the cached-system-prompt candidate query)
    already filters ``archived = 0`` — prove the writer actually shrinks it."""
    # Auto-inject-typed but NULL-typed noise can't demo this, so use a
    # visit note: type `fact` (auto-inject) yet archived by rule B.
    await _insert_note(
        cfg, "stale-visit",
        tags_json='["visit", "site-memory"]',
        created_at=OLD,
        memory_type="fact",
    )
    pool_before = {n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)}
    assert "stale-visit" in pool_before

    await _sweep(cfg)

    pool_after = {n["id"] for n in await note_store.list_memory_notes(cfg, _USER_ID)}
    assert "stale-visit" not in pool_after


async def test_updated_at_is_not_bumped(cfg):
    """Archiving must not disturb the offline-sync cursor.

    ``store.get_note_changes`` pulls the mobile delta off ``updated_at`` and
    does NOT filter archived rows — bumping it would push thousands of notes
    to every client on the next pull.
    """
    await _insert_note(cfg, "noise")
    await _sweep(cfg)
    async with db_session(cfg) as db:
        rows = await db.execute(
            "SELECT updated_at FROM notes WHERE id = ?", ("noise",)
        )
        assert (await rows.fetchone())[0] == OLD


async def test_empty_store_returns_zero(cfg):
    assert await _sweep(cfg) == 0


async def test_never_raises_on_broken_config(tmp_path: Path):
    """A maintenance sweep must not be able to take down the heartbeat tick."""
    broken = Config(database_dir=tmp_path / "does" / "not" / "exist")
    try:
        assert await archive_stale_auto_notes(broken, "u-nobody", now=_NOW) == 0
    finally:
        # The failed open may still have registered a pool slot; drop it so
        # the global pool can't leak into a later test's config.
        await close_pool()
