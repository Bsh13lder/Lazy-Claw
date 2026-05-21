"""Schema + write-path coverage for the PPR-grade ``note_links`` upgrade.

Covers (2026-05-21 work):
  * post-migration indexes ``idx_note_links_to_note`` and
    ``idx_note_links_user_kind`` are present after ``init_db``.
  * additive columns ``anchor`` / ``link_kind`` / ``occurrence`` exist
    with the right defaults — *both* on a fresh DB and after a migration
    of an old DB that pre-dates the columns.
  * the parser distinguishes ``[[X]]`` / ``![[X]]`` / ``[[X^block]]``
    by setting ``link_kind`` and stashing the block id in ``anchor``.
  * the write-path collapses repeated identical ``(from, to, anchor,
    link_kind)`` tuples into ONE row with ``occurrence=N``.
  * ``init_db`` is idempotent — running it twice on the same DB is a
    no-op (Docker restart loop safety, mirrors commit 11903f0).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store, wikilinks


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def tmp_config(tmp_path: Path):
    """Fresh DB + a registered user with a derivable DEK."""
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-links", "alice", "x", "salt-links-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


async def _table_columns(cfg: Config, table: str) -> dict[str, dict]:
    """Return ``{column_name: {type, notnull, dflt, ...}}`` for ``table``."""
    async with db_session(cfg) as db:
        rows = await db.execute(f"PRAGMA table_info({table})")
        cols = await rows.fetchall()
    # PRAGMA cols: cid, name, type, notnull, dflt_value, pk
    return {
        r[1]: {"type": r[2], "notnull": r[3], "dflt": r[4], "pk": r[5]}
        for r in cols
    }


async def _index_names(cfg: Config, table: str) -> set[str]:
    """Return the set of index names defined on ``table``."""
    async with db_session(cfg) as db:
        rows = await db.execute(f"PRAGMA index_list({table})")
        idxs = await rows.fetchall()
    # PRAGMA index_list: seq, name, unique, origin, partial
    return {r[1] for r in idxs}


# ── post-migration index presence ─────────────────────────────────────


async def test_idx_note_links_to_note_present_after_init(
    tmp_config: Config,
) -> None:
    """Backlink-by-id index unlocks the PPR hot path. Without it
    ``WHERE user_id=? AND to_note_id=?`` falls back to a text scan
    on ``to_page_name`` (which only covers UNresolved rows)."""
    names = await _index_names(tmp_config, "note_links")
    assert "idx_note_links_to_note" in names, (
        f"missing idx_note_links_to_note; have {sorted(names)}"
    )


async def test_idx_note_links_user_kind_present_after_init(
    tmp_config: Config,
) -> None:
    """Edge-type filter index speeds ``WHERE user_id=? AND edge_type=?``."""
    names = await _index_names(tmp_config, "note_links")
    assert "idx_note_links_user_kind" in names, (
        f"missing idx_note_links_user_kind; have {sorted(names)}"
    )


# ── additive columns + defaults ───────────────────────────────────────


async def test_new_columns_exist_with_correct_defaults(
    tmp_config: Config,
) -> None:
    """``anchor`` is nullable; ``link_kind`` defaults to ``'wikilink'``;
    ``occurrence`` defaults to 1 and is NOT NULL."""
    cols = await _table_columns(tmp_config, "note_links")
    assert "anchor" in cols
    assert cols["anchor"]["type"].upper() == "TEXT"
    assert cols["anchor"]["notnull"] == 0, "anchor must be nullable"

    assert "link_kind" in cols
    assert cols["link_kind"]["type"].upper() == "TEXT"
    assert cols["link_kind"]["notnull"] == 1, "link_kind must be NOT NULL"
    # SQLite stores defaults verbatim — single-quoted literal for TEXT.
    assert cols["link_kind"]["dflt"] in ("'wikilink'", "wikilink")

    assert "occurrence" in cols
    assert cols["occurrence"]["type"].upper() == "INTEGER"
    assert cols["occurrence"]["notnull"] == 1, "occurrence must be NOT NULL"
    assert str(cols["occurrence"]["dflt"]) == "1"


# ── parser: link_kind dispatch ────────────────────────────────────────


def test_parser_emits_kind_wikilink_for_plain_brackets() -> None:
    parsed = wikilinks.extract_wikilinks_full("see [[Alpha]]")
    assert len(parsed) == 1
    assert parsed[0].target == "alpha"
    assert parsed[0].link_kind == wikilinks.KIND_WIKILINK
    assert parsed[0].anchor == ""


def test_parser_emits_kind_embed_for_bang_brackets() -> None:
    parsed = wikilinks.extract_wikilinks_full("see ![[image.png]]")
    assert len(parsed) == 1
    assert parsed[0].target == "image.png"
    assert parsed[0].link_kind == wikilinks.KIND_EMBED


def test_parser_emits_kind_block_ref_for_caret() -> None:
    parsed = wikilinks.extract_wikilinks_full("see [[note^block-id]]")
    assert len(parsed) == 1
    assert parsed[0].target == "note"
    assert parsed[0].link_kind == wikilinks.KIND_BLOCK_REF
    assert parsed[0].anchor == "block-id"


def test_parser_dedupes_by_target_anchor_kind_tuple() -> None:
    """``[[X]]`` and ``![[X]]`` are distinct edges — must produce 2 rows."""
    parsed = wikilinks.extract_wikilinks_full("[[X]] then ![[X]]")
    kinds = sorted(w.link_kind for w in parsed)
    assert kinds == [wikilinks.KIND_EMBED, wikilinks.KIND_WIKILINK]


def test_parser_with_counts_collapses_identical_tuples() -> None:
    """``[[a]] [[a]] [[a]]`` → one record with occurrence=3."""
    out = wikilinks.extract_wikilinks_with_counts("[[a]] [[a]] [[a]]")
    assert len(out) == 1
    wl, count = out[0]
    assert wl.target == "a"
    assert count == 3


# ── write-path: persistence of new columns + counts ───────────────────


async def test_repeated_link_persists_as_single_row_with_occurrence(
    tmp_config: Config,
) -> None:
    """Obsidian-style: three ``[[a]]`` mentions in one note = one
    backlink row carrying occurrence=3."""
    await store.save_note(
        tmp_config, "user-links",
        content="See [[a]] and [[a]] and [[a]] for context.",
        title="Repeater",
    )
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT to_page_name, occurrence, link_kind, anchor "
            "FROM note_links WHERE user_id = ?",
            ("user-links",),
        )
        results = await rows.fetchall()
    # Filter just the wikilink edges (journal auto-link may be added too).
    a_rows = [r for r in results if r[0] == "a"]
    assert len(a_rows) == 1, f"expected one row for [[a]], got {a_rows}"
    assert a_rows[0][1] == 3, "occurrence must equal in-body link count"
    assert a_rows[0][2] == "wikilink"
    assert a_rows[0][3] is None


async def test_embed_persists_with_link_kind_embed(
    tmp_config: Config,
) -> None:
    await store.save_note(
        tmp_config, "user-links",
        content="Embedded: ![[image.png]]",
        title="Embedder",
    )
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT link_kind FROM note_links "
            "WHERE user_id = ? AND to_page_name = ?",
            ("user-links", "image.png"),
        )
        result = await rows.fetchone()
    assert result is not None, "embed [[…]] must produce a note_links row"
    assert result[0] == "embed"


async def test_block_ref_persists_with_anchor_and_link_kind(
    tmp_config: Config,
) -> None:
    await store.save_note(
        tmp_config, "user-links",
        content="See [[note^block-id]] for the exact paragraph.",
        title="BlockRefer",
    )
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT link_kind, anchor FROM note_links "
            "WHERE user_id = ? AND to_page_name = ?",
            ("user-links", "note"),
        )
        result = await rows.fetchone()
    assert result is not None
    assert result[0] == "block_ref"
    assert result[1] == "block-id"


async def test_anchor_persists_for_section_links(
    tmp_config: Config,
) -> None:
    """``[[Page#Section]]`` keeps the section name in anchor and
    stays link_kind=wikilink."""
    await store.save_note(
        tmp_config, "user-links",
        content="Refer to [[Page#Install Steps]] for setup.",
        title="Sectional",
    )
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT link_kind, anchor FROM note_links "
            "WHERE user_id = ? AND to_page_name = ?",
            ("user-links", "page"),
        )
        result = await rows.fetchone()
    assert result is not None
    assert result[0] == "wikilink"
    assert result[1] == "Install Steps"


# ── migration: old DB without new columns successfully upgrades ───────


async def test_old_db_without_new_columns_migrates(tmp_path: Path) -> None:
    """Simulate an upgrading install: hand-craft a ``note_links`` table
    using the pre-PPR schema (no anchor / link_kind / occurrence /
    edge_type / source / display_text), then run ``init_db`` and verify
    every new column is present afterwards."""
    db_file = tmp_path / "lazyclaw.db"
    # Synchronous sqlite3 so we don't bring up the async pool yet.
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "CREATE TABLE note_links ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id TEXT NOT NULL, "
            "from_note_id TEXT NOT NULL, "
            "to_page_name TEXT NOT NULL, "
            "to_note_id TEXT, "
            "created_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
        # Insert a pre-migration row so we can assert it still reads back.
        conn.execute(
            "INSERT INTO note_links "
            "(user_id, from_note_id, to_page_name) VALUES (?, ?, ?)",
            ("legacy-user", "legacy-note", "legacy-target"),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = Config(database_dir=tmp_path)
    try:
        await init_db(cfg)

        cols = await _table_columns(cfg, "note_links")
        for required in ("anchor", "link_kind", "occurrence",
                         "edge_type", "source", "display_text"):
            assert required in cols, (
                f"migration must add column {required!r}; "
                f"got columns: {sorted(cols)}"
            )

        # Pre-migration row should still be readable; new NOT-NULL
        # columns must fall back to their defaults for legacy rows.
        async with db_session(cfg) as db:
            rows = await db.execute(
                "SELECT link_kind, occurrence, anchor FROM note_links "
                "WHERE from_note_id = ?",
                ("legacy-note",),
            )
            row = await rows.fetchone()
        assert row is not None
        assert row[0] == "wikilink"
        assert row[1] == 1
        assert row[2] is None
    finally:
        await close_pool()


# ── idempotency: second init_db must succeed without errors ───────────


async def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Docker-restart-loop safety (mirrors commit 11903f0).
    Calling ``init_db`` twice on the same DB path must not raise."""
    cfg = Config(database_dir=tmp_path)
    try:
        await init_db(cfg)
        # Close the pool so the second call opens a fresh connection —
        # matches the Docker restart shape more faithfully.
        await close_pool()
        await init_db(cfg)

        # Sanity: indexes are still present after the second pass.
        names = await _index_names(cfg, "note_links")
        assert "idx_note_links_to_note" in names
        assert "idx_note_links_user_kind" in names
    finally:
        await close_pool()
