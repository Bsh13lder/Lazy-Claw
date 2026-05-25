"""Regression test: dirty-batch reindex must drain ALL dirty notes,
not just the 3 most-recent ones.

Locks the 2026-05-20 starvation fix.

Bug (pre-fix)
-------------
``reindex_dirty_batch`` did ``ORDER BY updated_at DESC LIMIT 50`` and
bailed after 3 consecutive embed failures. With 454 dirty notes for one
user — the top 3 of which were persistently broken (Ollama tokenizer
errors / decrypt failures) — every heartbeat tick re-picked the same
3, hit the same 3 skips, bailed before reaching the 451 others.
Process-level cooldown re-engaged endlessly. 451 notes never re-
embedded for hours.

Fix
---
- New column ``notes.last_reindex_attempt_at`` stamped on every
  attempt (success OR skip).
- Picker orders by ``last_reindex_attempt_at ASC NULLS FIRST`` so
  brand-new dirty rows have priority over previously-attempted ones,
  and the longest-ago attempt wins the next slot.
- Failed rows fall to the back of the queue, fair-share next cycle.

This test simulates 100 dirty notes where the first 3 (by save order)
permanently fail. Asserts that within N cycles ALL 100 are attempted
at least once — i.e. no starvation. Pre-fix, only 3 would ever be
touched no matter how many cycles you run.

Note on isolation
-----------------
``store.save_note`` schedules a fire-and-forget ``ensure_embedding``
task — that would race the test against itself (real-Ollama failure
mode plus phantom clears). We monkeypatch ``ensure_embedding`` to a
no-op so save_note's post-write hook stays inert and the test only
exercises the reindex path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import embeddings as _embeddings
from lazyclaw.lazybrain import store


@pytest.fixture
async def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh DB + a registered user with a derivable DEK.

    Also neutralises ``ensure_embedding`` so ``save_note``'s fire-and-
    forget post-write task can't race the reindex pass we're testing.
    Without this, the real ``ensure_embedding`` runs against a non-
    existent Ollama, returns False, but logs noisy debug — and worse,
    its timing is nondeterministic relative to the test's monkeypatch
    of ``upsert_embedding``.
    """
    async def _noop_ensure_embedding(*args, **kwargs):
        return None
    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.ensure_embedding",
        _noop_ensure_embedding,
    )

    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-reindex", "alice", "x", "salt-reindex-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


async def _force_dirty(cfg: Config, note_ids: Iterable[str]) -> None:
    """Re-mark a set of notes as dirty (save_note auto-clears the flag
    when the synchronous in-line embed in save_note succeeds; we need
    the reindex daemon path, not the inline path)."""
    async with db_session(cfg) as db:
        await db.executemany(
            "UPDATE notes SET embedding_dirty = 1, chunks_dirty = 1 "
            "WHERE id = ?",
            [(nid,) for nid in note_ids],
        )
        await db.commit()


async def _count_dirty(cfg: Config, user_id: str) -> int:
    async with db_session(cfg) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM notes "
            "WHERE user_id = ? "
            "AND (embedding_dirty = 1 OR chunks_dirty = 1)",
            (user_id,),
        )
        row = await rows.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.asyncio
async def test_reindex_drains_all_dirty_notes_when_first_three_fail(
    tmp_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """100 dirty notes where the first 3 (oldest) permanently fail.
    Within enough cycles, EVERY note must be attempted at least once.

    Pre-fix: only the 3 most-recent notes would ever be touched.
    Post-fix: per-row cooldown rotates the queue so all 100 drain.
    """
    user_id = "user-reindex"

    # Create 100 notes. Order matters for the test: notes[0..2] are the
    # "always-fail" trio; notes[3..99] are the "would-be-starved" set.
    note_ids: list[str] = []
    for i in range(100):
        n = await store.save_note(
            tmp_config, user_id,
            content=f"body-{i}", title=f"N{i:03d}",
        )
        note_ids.append(n["id"])

    # save_note attempts an inline embed via ensure_embedding; with no
    # Ollama in tests that's a no-op fail and the dirty flags stay set,
    # but be explicit so the fixture is robust regardless of harness
    # behaviour.
    await _force_dirty(tmp_config, note_ids)
    assert await _count_dirty(tmp_config, user_id) == 100

    fail_set: set[str] = set(note_ids[:3])
    attempted: set[str] = set()

    async def fake_upsert_embedding(
        config: Config, uid: str, note_id: str, text: str,
    ) -> bool:
        """Track every attempted note. Always fail for the poison trio;
        succeed (and clear dirty flag) for everything else."""
        attempted.add(note_id)
        if note_id in fail_set:
            return False
        # Mimic the real upsert_embedding's success side-effect: clear
        # both dirty flags so the note drops out of the picker's WHERE.
        async with db_session(config) as db:
            await db.execute(
                "UPDATE notes SET embedding_dirty = 0, chunks_dirty = 0 "
                "WHERE id = ? AND user_id = ?",
                (note_id, uid),
            )
            await db.commit()
        return True

    monkeypatch.setattr(
        _embeddings, "upsert_embedding", fake_upsert_embedding,
    )
    # Disable the process-level cooldown for the test — it would gate
    # everything after the first failed-only pass and the test would
    # have to manipulate wall clock. Cooldown behaviour is orthogonal
    # to starvation and covered by its own (existing) wiring.
    monkeypatch.setattr(_embeddings, "_REINDEX_COOLDOWN_SECS", 0.0)

    # Run enough cycles to drain. With limit=50 and only 3 poison rows
    # per cycle (bailout after 3 consecutive skips), each cycle clears
    # roughly limit−3 = 47 good rows plus stamps the 3 poison rows so
    # they rotate to the back. 97 good rows / 47-ish per cycle = ~3
    # cycles. Allow 10 to be safe against any ordering quirks.
    for _ in range(10):
        await _embeddings.reindex_dirty_batch(
            tmp_config, user_id, limit=50,
        )

    # Every non-poison note must have been embedded (dirty flag cleared).
    remaining_dirty = await _count_dirty(tmp_config, user_id)
    # Debug: which rows are still dirty?
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id, embedding_dirty, chunks_dirty FROM notes "
            "WHERE user_id = ? "
            "AND (embedding_dirty = 1 OR chunks_dirty = 1)",
            (user_id,),
        )
        still_dirty = await rows.fetchall()
    poison_set_dirty = [r for r in still_dirty if r[0] in fail_set]
    other_dirty = [r for r in still_dirty if r[0] not in fail_set]
    # The 3 poison notes stay dirty (they always fail); everything else
    # must be drained.
    assert remaining_dirty == 3, (
        f"expected only the 3 poison notes to remain dirty; "
        f"got {remaining_dirty} — starvation regression. "
        f"poison-still-dirty={len(poison_set_dirty)} "
        f"other-still-dirty={len(other_dirty)} "
        f"other-ids={[r[0][:8] for r in other_dirty[:5]]}"
    )

    # And every note must have been ATTEMPTED at least once, including
    # the deepest one. Pre-fix, attempted would equal {note_ids[0..2]}.
    missed = set(note_ids) - attempted
    assert not missed, (
        f"{len(missed)} notes never attempted — starvation regression. "
        f"first missed ids: {sorted(missed)[:5]}"
    )


@pytest.mark.asyncio
async def test_reindex_stamps_last_attempt_on_success_and_skip(
    tmp_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both success AND skip paths must stamp last_reindex_attempt_at,
    so a failed row falls to the back of the queue next cycle.
    """
    user_id = "user-reindex"
    good = await store.save_note(
        tmp_config, user_id, content="good", title="Good",
    )
    bad = await store.save_note(
        tmp_config, user_id, content="bad", title="Bad",
    )
    await _force_dirty(tmp_config, [good["id"], bad["id"]])

    async def fake_upsert(
        config: Config, uid: str, note_id: str, text: str,
    ) -> bool:
        if note_id == bad["id"]:
            return False
        async with db_session(config) as db:
            await db.execute(
                "UPDATE notes SET embedding_dirty = 0, chunks_dirty = 0 "
                "WHERE id = ? AND user_id = ?",
                (note_id, uid),
            )
            await db.commit()
        return True

    monkeypatch.setattr(_embeddings, "upsert_embedding", fake_upsert)
    monkeypatch.setattr(_embeddings, "_REINDEX_COOLDOWN_SECS", 0.0)

    await _embeddings.reindex_dirty_batch(tmp_config, user_id, limit=10)

    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id, last_reindex_attempt_at FROM notes "
            "WHERE user_id = ? ORDER BY title_key",
            (user_id,),
        )
        stamps = {nid: ts for nid, ts in await rows.fetchall()}

    assert stamps[good["id"]] is not None, (
        "success path must stamp last_reindex_attempt_at"
    )
    assert stamps[bad["id"]] is not None, (
        "skip path must stamp last_reindex_attempt_at — "
        "otherwise failed rows re-elect themselves and starve others"
    )


@pytest.mark.asyncio
async def test_picker_prefers_never_attempted_over_recently_attempted(
    tmp_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NULLS FIRST semantics: a brand-new dirty row (column = NULL)
    must be picked before a previously-attempted dirty row, regardless
    of updated_at ordering. This is the property that lets fresh user
    edits get embedded promptly even when the queue has stale stragglers.
    """
    user_id = "user-reindex"

    # Stale: previously attempted (timestamp manually planted) but
    # still dirty (e.g. embed kept failing).
    stale = await store.save_note(
        tmp_config, user_id, content="stale", title="Stale",
    )
    # Mark it as attempted long ago.
    async with db_session(tmp_config) as db:
        await db.execute(
            "UPDATE notes SET embedding_dirty = 1, "
            "last_reindex_attempt_at = '2020-01-01 00:00:00' "
            "WHERE id = ?",
            (stale["id"],),
        )
        await db.commit()

    # Fresh: brand-new dirty row — last_reindex_attempt_at IS NULL.
    fresh = await store.save_note(
        tmp_config, user_id, content="fresh", title="Fresh",
    )
    await _force_dirty(tmp_config, [fresh["id"]])
    # Defensive: clear any stamp the inline save_note path may have set.
    async with db_session(tmp_config) as db:
        await db.execute(
            "UPDATE notes SET last_reindex_attempt_at = NULL "
            "WHERE id = ?",
            (fresh["id"],),
        )
        await db.commit()

    picked: list[str] = []

    async def fake_upsert(
        config: Config, uid: str, note_id: str, text: str,
    ) -> bool:
        picked.append(note_id)
        # Don't actually clear the flag — we only care about pick order.
        return False

    monkeypatch.setattr(_embeddings, "upsert_embedding", fake_upsert)
    monkeypatch.setattr(_embeddings, "_REINDEX_COOLDOWN_SECS", 0.0)

    await _embeddings.reindex_dirty_batch(tmp_config, user_id, limit=10)

    assert picked, "picker returned nothing despite 2 dirty rows"
    assert picked[0] == fresh["id"], (
        f"picker must serve NULL-attempt rows FIRST. "
        f"got order: {picked}, expected fresh={fresh['id']} first"
    )
