"""DB-backed integration tests for the second-brain substrate.

Covers:
  - notes.aliases column + frontmatter aliases passthrough
  - find_by_title falls through to aliases on title miss
  - wikilink collision detection writes audit_log entry
  - add_relation / list_relations roundtrip with edge_type filter
  - _reindex_links preserves typed edges, only wipes wikilink rows
  - notes.embedding_dirty flag set on insert + cleared semantics
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store


@pytest.fixture
async def tmp_config(tmp_path: Path):
    """Fresh DB + a registered user with a derivable DEK."""
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-sub", "alice", "x", "salt-sub-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# ─── Aliases ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aliases_param_persists_and_resolves(tmp_config: Config) -> None:
    """save_note(aliases=[...]) stores plaintext JSON; find_by_title falls
    through alias lookup when title_key misses."""
    note = await store.save_note(
        tmp_config, "user-sub",
        content="Spanish tax authority — pay quarterly VAT here.",
        title="Agencia Tributaria",
        aliases=["SAT", "Tax Office", "Hacienda"],
    )

    # title_key resolution still works
    by_title = await store.find_by_title(
        tmp_config, "user-sub", "Agencia Tributaria",
    )
    assert by_title is not None
    assert by_title["id"] == note["id"]

    # alias resolution kicks in for distinct names
    by_alias = await store.find_by_title(tmp_config, "user-sub", "SAT")
    assert by_alias is not None, "alias lookup must resolve when title misses"
    assert by_alias["id"] == note["id"]

    by_alias2 = await store.find_by_title(tmp_config, "user-sub", "Tax Office")
    assert by_alias2 is not None
    assert by_alias2["id"] == note["id"]


@pytest.mark.asyncio
async def test_frontmatter_aliases_passthrough(tmp_config: Config) -> None:
    """Aliases supplied via frontmatter (Obsidian-style) flow into the
    notes.aliases column without an explicit aliases= param."""
    await store.save_note(
        tmp_config, "user-sub",
        content="The annual flu vaccine reminder lives here.",
        title="Vaccine schedule",
        frontmatter={"aliases": ["flu shot", "vaccination"]},
    )
    hit = await store.find_by_title(tmp_config, "user-sub", "flu shot")
    assert hit is not None
    assert hit["title"] == "Vaccine schedule"


@pytest.mark.asyncio
async def test_title_match_wins_over_alias_collision(tmp_config: Config) -> None:
    """If a real title matches the wikilink, alias rows for the same
    string must NOT win. Title-key resolution is authoritative."""
    real = await store.save_note(
        tmp_config, "user-sub",
        content="Capital city of Spain", title="Madrid",
    )
    await store.save_note(
        tmp_config, "user-sub",
        content="Where I live", title="Home",
        aliases=["Madrid"],  # alias collides with real title
    )
    hit = await store.find_by_title(tmp_config, "user-sub", "Madrid")
    assert hit is not None
    assert hit["id"] == real["id"], "title match must beat alias match"


# ─── Wikilink collision detection ──────────────────────────────────────


@pytest.mark.asyncio
async def test_wikilink_collision_logs_audit_entry(tmp_config: Config) -> None:
    """Two notes sharing the same title_key + a third note linking to
    them produces a wikilink_collision audit_log entry, but both
    candidates remain queryable via list_notes."""
    await store.save_note(
        tmp_config, "user-sub",
        content="Old appointment system from 2024.",
        title="Cita Previa",
    )
    await store.save_note(
        tmp_config, "user-sub",
        content="New appointment system from 2026.",
        title="Cita Previa",
    )
    # A third note links to it — triggers collision detection inside
    # _reindex_links (the link target resolution).
    await store.save_note(
        tmp_config, "user-sub",
        content="Need to book a [[Cita Previa]] for the consulate.",
        title="Travel todo",
    )

    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT result_summary FROM audit_log "
            "WHERE user_id = ? AND action = 'wikilink_collision'",
            ("user-sub",),
        )
        results = await rows.fetchall()
    assert len(results) >= 1, (
        "writing a [[Cita Previa]] link with two candidates must produce "
        "at least one wikilink_collision audit row"
    )
    assert "cita previa" in results[0][0].lower()


# ─── Typed-edge substrate ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_relation_roundtrip_and_idempotent(tmp_config: Config) -> None:
    """add_relation writes a typed edge; re-emission replaces (not duplicates)."""
    a = await store.save_note(
        tmp_config, "user-sub", content="lesson v1", title="Skill v1",
    )
    b = await store.save_note(
        tmp_config, "user-sub", content="lesson v2", title="Skill v2",
    )

    ok = await store.add_relation(
        tmp_config, "user-sub",
        from_note_id=b["id"], to_note_id=a["id"],
        kind="supersedes", source="skill_lesson_auto",
    )
    assert ok is True

    edges = await store.list_relations(
        tmp_config, "user-sub", b["id"], direction="out", kind="supersedes",
    )
    assert len(edges) == 1
    assert edges[0]["edge_type"] == "supersedes"
    assert edges[0]["source"] == "skill_lesson_auto"
    assert edges[0]["to_note_id"] == a["id"]

    # Re-emission with same (from, to, kind, source) replaces, not duplicates
    await store.add_relation(
        tmp_config, "user-sub",
        from_note_id=b["id"], to_note_id=a["id"],
        kind="supersedes", source="skill_lesson_auto",
    )
    edges_after = await store.list_relations(
        tmp_config, "user-sub", b["id"], direction="out", kind="supersedes",
    )
    assert len(edges_after) == 1, "idempotent re-emission must not duplicate"


@pytest.mark.asyncio
async def test_add_relation_rejects_unknown_kind(tmp_config: Config) -> None:
    a = await store.save_note(
        tmp_config, "user-sub", content="x", title="X",
    )
    b = await store.save_note(
        tmp_config, "user-sub", content="y", title="Y",
    )
    with pytest.raises(ValueError):
        await store.add_relation(
            tmp_config, "user-sub",
            from_note_id=a["id"], to_note_id=b["id"],
            kind="invalid_kind", source="test",
        )


@pytest.mark.asyncio
async def test_add_relation_refuses_cross_user_edges(tmp_config: Config) -> None:
    """If the to_note doesn't belong to the requesting user, the edge
    is silently refused (returns False, doesn't raise)."""
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-sub-2", "bob", "x", "salt-other"),
        )
        await db.commit()
    a = await store.save_note(
        tmp_config, "user-sub", content="my note", title="A",
    )
    b = await store.save_note(
        tmp_config, "user-sub-2", content="bob's note", title="B",
    )
    ok = await store.add_relation(
        tmp_config, "user-sub",
        from_note_id=a["id"], to_note_id=b["id"],  # b belongs to bob
        kind="references", source="test",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_reindex_links_preserves_typed_edges(tmp_config: Config) -> None:
    """When a note's content is rewritten, _reindex_links must wipe its
    OWN wikilink rows but leave typed edges (supersedes/references/…)
    alone — otherwise every save_note would erase the typed graph."""
    a = await store.save_note(
        tmp_config, "user-sub", content="target", title="Target",
    )
    src = await store.save_note(
        tmp_config, "user-sub",
        content="Initial body with [[Target]] link.",
        title="Source",
    )
    # Add a typed edge from src → a.
    await store.add_relation(
        tmp_config, "user-sub",
        from_note_id=src["id"], to_note_id=a["id"],
        kind="references", source="manual",
    )

    # Rewrite src — drops the wikilink and writes new content.
    await store.update_note(
        tmp_config, "user-sub", src["id"],
        content="Rewritten body, no wikilink any more.",
    )

    out_edges = await store.list_relations(
        tmp_config, "user-sub", src["id"], direction="out",
    )
    edge_types = [e["edge_type"] for e in out_edges]
    assert "references" in edge_types, (
        "typed edge must survive content rewrite — wipe was too broad"
    )
    # The wikilink row from the original [[Target]] should be gone now
    # (it was reindexed; new content has no [[link]]).
    assert "wikilink" not in edge_types, (
        "the original [[Target]] wikilink should have been re-indexed away"
    )


# ─── embedding_dirty flag wiring ───────────────────────────────────────


@pytest.mark.asyncio
async def test_save_note_sets_embedding_dirty(tmp_config: Config) -> None:
    """Newly created notes start with embedding_dirty=1 so the heartbeat
    reindex pass picks them up if Ollama was offline at save time."""
    note = await store.save_note(
        tmp_config, "user-sub",
        content="content needing an embedding", title="N",
    )
    async with db_session(tmp_config) as db:
        row = await db.execute(
            "SELECT embedding_dirty FROM notes WHERE id = ?",
            (note["id"],),
        )
        r = await row.fetchone()
    assert r is not None
    assert r[0] == 1, "fresh notes must start dirty"


@pytest.mark.asyncio
async def test_update_note_content_marks_dirty(tmp_config: Config) -> None:
    note = await store.save_note(
        tmp_config, "user-sub", content="v1", title="N",
    )
    # Manually clear the flag (simulating a successful embedding upsert).
    async with db_session(tmp_config) as db:
        await db.execute(
            "UPDATE notes SET embedding_dirty = 0 WHERE id = ?",
            (note["id"],),
        )
        await db.commit()

    await store.update_note(
        tmp_config, "user-sub", note["id"], content="v2 — different body",
    )
    async with db_session(tmp_config) as db:
        row = await db.execute(
            "SELECT embedding_dirty FROM notes WHERE id = ?",
            (note["id"],),
        )
        r = await row.fetchone()
    assert r[0] == 1, "content edit must re-mark dirty"


@pytest.mark.asyncio
async def test_archived_notes_hidden_by_default(tmp_config: Config) -> None:
    """list_notes hides archived rows; include_archived=True surfaces them.
    Powers the skills-vault toggle."""
    keep = await store.save_note(
        tmp_config, "user-sub", content="visible", title="Keep",
    )
    archived = await store.save_note(
        tmp_config, "user-sub", content="hidden", title="Archived",
    )
    async with db_session(tmp_config) as db:
        await db.execute(
            "UPDATE notes SET archived = 1 WHERE id = ?",
            (archived["id"],),
        )
        await db.commit()

    default_listing = await store.list_notes(tmp_config, "user-sub")
    ids_default = {n["id"] for n in default_listing}
    assert keep["id"] in ids_default
    assert archived["id"] not in ids_default

    full_listing = await store.list_notes(
        tmp_config, "user-sub", include_archived=True,
    )
    ids_full = {n["id"] for n in full_listing}
    assert archived["id"] in ids_full
