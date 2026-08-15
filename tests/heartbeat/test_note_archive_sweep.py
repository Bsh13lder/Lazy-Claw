"""Heartbeat wiring for the LazyBrain note-archive retention sweep.

``archive_stale_auto_notes`` is only useful if something actually runs it.
This is the wiring smoke test: the daemon must call it once per user per
day, scope it to users who own notes, stamp the marker only on success, and
never let a failure escape into the tick.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.daemon import HeartbeatDaemon
from lazyclaw.lazybrain.store import _content_aad, _title_aad

pytestmark = pytest.mark.asyncio

_USER_ID = "u-hb-archive"
_QUIET_USER_ID = "u-hb-quiet"
_OLD = "2026-01-01 09:00:00.000000"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        for uid, uname in ((_USER_ID, "hbarch"), (_QUIET_USER_ID, "hbquiet")):
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


async def _insert_stale_auto_note(cfg: Config, note_id: str, user_id: str) -> None:
    """An old, low-importance, session-log-typed ``#auto`` note — the
    canonical archivable shape (see tests/lazybrain/test_archive_stale_auto_notes)."""
    dek = await get_user_dek(cfg, user_id)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, importance, "
            "pinned, title_key, memory_type, archived, embedding_dirty, "
            "deleted_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '[\"auto\"]', 4, 0, ?, 'session-log', 0, 0, "
            "NULL, ?, ?)",
            (
                note_id,
                user_id,
                encrypt_field(note_id, dek, _title_aad(user_id)),
                encrypt_field("body", dek, _content_aad(user_id)),
                note_id,
                _OLD,
                _OLD,
            ),
        )
        await db.commit()


def _daemon(cfg: Config) -> HeartbeatDaemon:
    return HeartbeatDaemon(cfg, lane_queue=None)


async def _archived_count(cfg: Config, user_id: str) -> int:
    async with db_session(cfg) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ? AND archived = 1",
            (user_id,),
        )
        return (await rows.fetchone())[0]


async def test_sweep_archives_stale_notes(cfg):
    await _insert_stale_auto_note(cfg, "n1", _USER_ID)
    await _insert_stale_auto_note(cfg, "n2", _USER_ID)

    daemon = _daemon(cfg)
    await daemon._sweep_note_archive()

    assert await _archived_count(cfg, _USER_ID) == 2


async def test_sweep_runs_once_per_user_per_day(cfg):
    """The per-user ISO marker must short-circuit a second tick the same day."""
    await _insert_stale_auto_note(cfg, "n1", _USER_ID)
    daemon = _daemon(cfg)

    calls: list[str] = []
    from lazyclaw.lazybrain import maintenance as _maint

    real = _maint.archive_stale_auto_notes

    async def _counting(config, user_id, **kw):
        calls.append(user_id)
        return await real(config, user_id, **kw)

    _maint.archive_stale_auto_notes = _counting
    try:
        await daemon._sweep_note_archive()
        await daemon._sweep_note_archive()
        await daemon._sweep_note_archive()
    finally:
        _maint.archive_stale_auto_notes = real

    assert calls == [_USER_ID], "sweep re-ran within the same day"
    assert daemon._last_note_archive_iso.get(_USER_ID)


async def test_sweep_skips_users_with_no_notes(cfg):
    """Only users who own notes are visited — dead accounts cost nothing."""
    await _insert_stale_auto_note(cfg, "n1", _USER_ID)
    daemon = _daemon(cfg)
    await daemon._sweep_note_archive()

    assert _USER_ID in daemon._last_note_archive_iso
    assert _QUIET_USER_ID not in daemon._last_note_archive_iso


async def test_sweep_is_per_user_isolated(cfg):
    await _insert_stale_auto_note(cfg, "mine", _USER_ID)
    await _insert_stale_auto_note(cfg, "theirs", _QUIET_USER_ID)

    daemon = _daemon(cfg)
    await daemon._sweep_note_archive()

    assert await _archived_count(cfg, _USER_ID) == 1
    assert await _archived_count(cfg, _QUIET_USER_ID) == 1
    assert set(daemon._last_note_archive_iso) == {_USER_ID, _QUIET_USER_ID}


async def test_sweep_never_raises_and_retries_next_tick(cfg):
    """A failing sweep must not escape the tick, and must NOT stamp the
    marker — otherwise a transient error would skip the whole day."""
    await _insert_stale_auto_note(cfg, "n1", _USER_ID)
    daemon = _daemon(cfg)

    from lazyclaw.lazybrain import maintenance as _maint

    real = _maint.archive_stale_auto_notes

    async def _explode(*_a, **_kw):
        raise RuntimeError("boom")

    _maint.archive_stale_auto_notes = _explode
    try:
        await daemon._sweep_note_archive()  # must not raise
    finally:
        _maint.archive_stale_auto_notes = real

    assert _USER_ID not in daemon._last_note_archive_iso

    await daemon._sweep_note_archive()
    assert await _archived_count(cfg, _USER_ID) == 1


async def test_sweep_survives_missing_notes_table(tmp_path: Path):
    """Listing users can't take down the tick either."""
    broken = Config(database_dir=tmp_path / "nope")
    daemon = _daemon(broken)
    try:
        await daemon._sweep_note_archive()  # must not raise
    finally:
        await close_pool()


async def test_tick_invokes_the_sweep(cfg, monkeypatch):
    """Pin the call site: the sweep must be wired into ``_tick``, not just
    exist as an orphan method."""
    daemon = _daemon(cfg)
    fired: list[int] = []

    async def _noop():
        return None

    async def _record():
        fired.append(1)

    # Neutralise every other tick pass so this stays a wiring assertion.
    for name in (
        "_check_task_nagging", "_seed_today_journals", "_sweep_topic_rollups",
        "_reindex_dirty_embeddings", "_retry_lazybrain_mirrors",
        "_sweep_stale_progress", "_sweep_eod_summary", "_reconcile_awake_mode",
        "_ensure_persistent_browser",
    ):
        monkeypatch.setattr(daemon, name, _noop)
    monkeypatch.setattr(daemon, "_sweep_note_archive", _record)

    await daemon._tick()
    assert fired == [1], "_sweep_note_archive is not wired into _tick"
