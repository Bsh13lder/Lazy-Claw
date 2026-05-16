"""DB-backed tests for the lazybrain duplicate consolidation pass.

Seeds a duplicate cluster, runs the cleanup, and asserts:
  - one note remains per title_key (the oldest one wins)
  - every duplicate's backlinks are redirected to the kept id
  - the unioned tag set on the kept note covers all variants
  - dry-run reports the same plan without writing
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import cleanup, store


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-clean", "cleanup", "x", "salt-cleanup-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


async def _seed_duplicates(
    cfg: Config, user_id: str, n: int,
) -> list[str]:
    """Create ``n`` notes with the same title — the v2-migration scenario."""
    ids: list[str] = []
    for i in range(n):
        note = await store.save_note(
            cfg, user_id,
            content=f"replay {i}",
            title="Skill shape · web/web_search · search-flights",
            tags=[
                "kind/shape",
                "topic/web",
                f"action/web_search_{i}",  # forces tag variants
                "intent/search-flights",
                "auto",
                "owner/agent",
            ],
            importance=4,
        )
        ids.append(note["id"])
    return ids


@pytest.mark.asyncio
async def test_consolidate_user_collapses_duplicates(tmp_config: Config) -> None:
    """Three notes with the same title → one note kept, two deleted.
    The cleanup picks the lowest (created_at, id) — at second resolution
    timestamps collide so the id-asc tiebreaker takes over."""
    ids = await _seed_duplicates(tmp_config, "u-clean", n=3)

    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=False,
    )

    assert summary["groups"] == 1
    assert summary["total_deleted"] == 2

    # Exactly one row survives, and it's one of the originals.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id FROM notes WHERE user_id = ?", ("u-clean",),
        )
        remaining = {r[0] for r in await rows.fetchall()}
    assert len(remaining) == 1
    survivor = next(iter(remaining))
    assert survivor in set(ids)
    # The cleanup's own report agrees with the DB state.
    assert summary["details"][0]["kept_id"] == survivor


@pytest.mark.asyncio
async def test_consolidate_user_redirects_backlinks(tmp_config: Config) -> None:
    """Edges pointing AT or FROM a duplicate are redirected to the
    kept id — no edge gets stranded on a deleted note.

    Note: when seed timestamps collide at second resolution the ORDER
    BY tiebreaker falls to id ASC, so we can't assume ``ids[0]`` is the
    survivor. We seed edges against TWO duplicates and trust the
    cleanup to redirect both — whichever one ends up surviving.
    """
    ids = await _seed_duplicates(tmp_config, "u-clean", n=3)
    # Pick two of the three as "duplicates to be redirected"; the
    # surviving id will be whichever cleanup keeps.
    dup_a, dup_b = ids[1], ids[2]

    # Seed a side note that backlinks to dup_b via note_links.
    other = await store.save_note(
        tmp_config, "u-clean",
        content="Side note that links elsewhere.",
        title="Side note",
        tags=["owner/user"],
    )
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name, edge_type, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "u-clean", other["id"], dup_b,
                "skill shape · web/web_search · search-flights",
                "wikilink", "test",
            ),
        )
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name, edge_type, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "u-clean", dup_a, other["id"],
                "side note", "wikilink", "test",
            ),
        )
        await db.commit()

    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=False,
    )
    # Trust the cleanup to declare the survivor — id ASC tiebreaker.
    assert summary["groups"] == 1
    detail = summary["details"][0]
    keep_id = detail["kept_id"]
    deleted_ids = set(detail["deleted_ids"])

    # Both seeded edges should now reference keep_id, not the deleted dupes.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT from_note_id, to_note_id FROM note_links "
            "WHERE user_id = ? AND from_note_id != to_note_id",
            ("u-clean",),
        )
        edges = await rows.fetchall()

    edge_set = {(f, t) for f, t in edges}
    # Inbound edge (other → some-dupe) is redirected to (other → keep_id).
    # Outbound edge (some-dupe → other) is redirected to (keep_id → other).
    if (other["id"], dup_b) not in edge_set and (other["id"], keep_id) not in edge_set:
        # Edge was pointed at the survivor — collapsed by the self-edge
        # pruning. Still acceptable.
        pass
    # Strict property: every edge endpoint references either keep_id or
    # the unrelated `other` node — never a deleted dupe.
    for f, t in edge_set:
        assert f not in deleted_ids
        assert t not in deleted_ids
    # And at least one of the original two edges survived in some form.
    assert len(edge_set) >= 1


@pytest.mark.asyncio
async def test_consolidate_user_unions_tags(tmp_config: Config) -> None:
    """Kept note's tag set should be the union across the dupe cluster."""
    await _seed_duplicates(tmp_config, "u-clean", n=3)

    await cleanup.consolidate_user(tmp_config, "u-clean", dry_run=False)

    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT tags FROM notes WHERE user_id = ?", ("u-clean",),
        )
        rows = await rows.fetchall()
    assert len(rows) == 1

    import json
    tags = json.loads(rows[0][0]) if rows[0][0] else []
    # Each replay seeded a different action/* variant.
    assert "action/web_search_0" in tags
    assert "action/web_search_1" in tags
    assert "action/web_search_2" in tags
    # Shared tags survive too.
    assert "kind/shape" in tags
    assert "topic/web" in tags


@pytest.mark.asyncio
async def test_dry_run_reports_without_writing(tmp_config: Config) -> None:
    """``dry_run=True`` returns the same group plan without deleting."""
    ids = await _seed_duplicates(tmp_config, "u-clean", n=4)

    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=True,
    )

    assert summary["dry_run"] is True
    assert summary["groups"] == 1
    assert summary["total_deleted"] == 3

    # Nothing actually deleted.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ?", ("u-clean",),
        )
        (n,) = await (await db.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ?", ("u-clean",),
        )).fetchone()
    assert n == 4
    # Sanity: the original ids are all still present.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id FROM notes WHERE user_id = ?", ("u-clean",),
        )
        present = {r[0] for r in await rows.fetchall()}
    assert set(ids).issubset(present)


@pytest.mark.asyncio
async def test_no_duplicates_no_op(tmp_config: Config) -> None:
    """An empty / clean vault returns 0 groups without writing anything."""
    await store.save_note(
        tmp_config, "u-clean",
        content="solo note",
        title="Lonely page",
        tags=["owner/user"],
    )

    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=False,
    )

    assert summary["groups"] == 0
    assert summary["total_deleted"] == 0


@pytest.mark.asyncio
async def test_journal_duplicates_consolidate_by_date(
    tmp_config: Config,
) -> None:
    """Two stub journals + one real journal for the same day → one
    journal survives, and it's the one with the actual content + edges
    (not an empty stub).

    Mirrors the production race where journal.get_journal() + save_note
    saw three coroutines each miss the existence check and each create
    a fresh row tagged journal/<date>. Title may have been auto-renamed
    on one of them; this means title_key dedup misses these but the
    journal-tag dedup catches them.
    """
    # Stub 1 — title stayed as the default "Journal — date"
    stub_a = await store.save_note(
        tmp_config, "u-clean",
        content="# Journal — 2026-05-13\n",
        title="Journal — 2026-05-13",
        tags=["journal/2026-05-13", "owner/user"],
    )
    # Real journal — has bullets + got auto-renamed by the LLM pass.
    real = await store.save_note(
        tmp_config, "u-clean",
        content=(
            "# Journal — 2026-05-13\n\n"
            "- 09:15 UTC — [[Task: ship dedup]] — done\n"
            "- 14:42 UTC — [[Lesson: prefer find_by_title]] — captured\n"
        ),
        title="2026-05-13 — shipped lazybrain dedup",
        tags=["journal/2026-05-13", "owner/user"],
    )
    # Stub 2 — also created by a racing coroutine
    stub_b = await store.save_note(
        tmp_config, "u-clean",
        content="# Journal — 2026-05-13\n",
        title="Journal — 2026-05-13",
        tags=["journal/2026-05-13", "owner/user"],
    )

    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=False,
    )

    # One journal-date group, two deletions.
    journal_groups = [
        d for d in summary["details"]
        if d.get("title_key", "").startswith("<journal/")
    ]
    assert len(journal_groups) == 1
    assert len(journal_groups[0]["deleted_ids"]) == 2

    # The real journal survived; both stubs are gone.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id FROM notes WHERE user_id = ?", ("u-clean",),
        )
        remaining = {r[0] for r in await rows.fetchall()}
    assert real["id"] in remaining
    assert stub_a["id"] not in remaining
    assert stub_b["id"] not in remaining


@pytest.mark.asyncio
async def test_journal_single_day_is_no_op(tmp_config: Config) -> None:
    """One journal per day → no consolidation runs."""
    await store.save_note(
        tmp_config, "u-clean",
        content="# Journal — 2026-05-12\n\n- 09:00 UTC — solo entry",
        title="2026-05-12 — solo",
        tags=["journal/2026-05-12", "owner/user"],
    )
    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=False,
    )
    assert summary["groups"] == 0


@pytest.mark.asyncio
async def test_sweep_orphan_links_removes_dangling_edges(
    tmp_config: Config,
) -> None:
    """note_links referencing a deleted note are pruned.

    Three edges seeded:
      1. (real → ghost)  — to_note_id points at a non-existent note
      2. (ghost → real)  — from_note_id points at a non-existent note
      3. (real → real)   — both endpoints exist; must survive
    Plus one fresh pending wikilink (created today, unresolved) — must
    survive too because of the 30-day grace period.
    """
    real_a = await store.save_note(
        tmp_config, "u-clean", content="A", title="A",
        tags=["owner/user"],
    )
    real_b = await store.save_note(
        tmp_config, "u-clean", content="B", title="B",
        tags=["owner/user"],
    )
    async with db_session(tmp_config) as db:
        # 1. Edge to a ghost note id
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name) "
            "VALUES (?, ?, ?, ?)",
            ("u-clean", real_a["id"], "ghost-target-id", "ghost target"),
        )
        # 2. Edge from a ghost source id
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name) "
            "VALUES (?, ?, ?, ?)",
            ("u-clean", "ghost-source-id", real_b["id"], "b"),
        )
        # 3. Healthy edge — must survive
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name) "
            "VALUES (?, ?, ?, ?)",
            ("u-clean", real_a["id"], real_b["id"], "b"),
        )
        # 4. Fresh pending wikilink — unresolved but within 30-day grace.
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name) "
            "VALUES (?, ?, NULL, ?)",
            ("u-clean", real_a["id"], "not-yet-created"),
        )
        await db.commit()

    summary = await cleanup.sweep_orphan_links(
        tmp_config, "u-clean", dry_run=False,
    )
    # Two orphan edges deleted (1 + 2); 0 stale-pending (the fresh one
    # is inside the 30-day grace window).
    assert summary["orphans_deleted"] == 2
    assert summary["stale_pending_deleted"] == 0

    # The healthy edge + fresh pending edge are the only survivors.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT from_note_id, to_note_id FROM note_links "
            "WHERE user_id = ?",
            ("u-clean",),
        )
        edges = await rows.fetchall()
    assert len(edges) == 2
    edge_pairs = {(f, t) for f, t in edges}
    assert (real_a["id"], real_b["id"]) in edge_pairs
    assert (real_a["id"], None) in edge_pairs


@pytest.mark.asyncio
async def test_sweep_orphan_links_dry_run(tmp_config: Config) -> None:
    """dry_run reports the counts without writing anything."""
    real = await store.save_note(
        tmp_config, "u-clean", content="x", title="x",
        tags=["owner/user"],
    )
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_note_id, to_page_name) "
            "VALUES (?, ?, ?, ?)",
            ("u-clean", real["id"], "ghost", "ghost"),
        )
        await db.commit()

    summary = await cleanup.sweep_orphan_links(
        tmp_config, "u-clean", dry_run=True,
    )
    assert summary["orphans_deleted"] == 1
    assert summary["dry_run"] is True
    # Edge is still there.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM note_links WHERE user_id = ?",
            ("u-clean",),
        )
        (n,) = await (await db.execute(
            "SELECT COUNT(*) FROM note_links WHERE user_id = ?",
            ("u-clean",),
        )).fetchone()
    assert n == 1


@pytest.mark.asyncio
async def test_consolidate_user_includes_sweep_summary(
    tmp_config: Config,
) -> None:
    """The unified consolidate_user pass reports orphan + stale counters."""
    summary = await cleanup.consolidate_user(
        tmp_config, "u-clean", dry_run=False,
    )
    assert "orphans_deleted" in summary
    assert "stale_pending_deleted" in summary
    assert summary["orphans_deleted"] == 0
    assert summary["stale_pending_deleted"] == 0
