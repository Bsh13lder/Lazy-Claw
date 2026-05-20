"""Tests for the Phase 3 typed-memory recall path.

Covers:
  - Schema validation: empty input rejected, unknown memory_type coerced
    to ``fact`` (never raises).
  - memory_type filter: only rows matching the requested type surface.
  - topic match: notes with a matching title_key are returned.
  - wikilink crawl: notes linking AT the topic title are returned even
    when they don't carry the requested memory_type — proves the graph
    edge is used as a query path.
  - limit honored.
  - Returned dict shape includes the keys the brain needs to ground a
    reply.

The classifier itself is exercised by
``tests/test_lazybrain_memory_types.py`` — this file pins the recall
composition (semantic + wikilink + backfill + topic injection).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store, typed_recall
from lazyclaw.skills.builtin.lazybrain.recall_typed_memory import (
    RecallTypedMemorySkill,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    """Fresh DB + a registered user with a derivable DEK."""
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-typed", "alice", "x", "salt-typed-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# Schema + validation


@pytest.mark.asyncio
async def test_query_rejects_all_none(tmp_config):
    with pytest.raises(ValueError):
        await typed_recall.query_by_memory_type(tmp_config, "user-typed")


@pytest.mark.asyncio
async def test_recall_rejects_all_none(tmp_config):
    with pytest.raises(ValueError):
        await typed_recall.recall_typed_memory(tmp_config, "user-typed")


@pytest.mark.asyncio
async def test_recall_rejects_only_whitespace(tmp_config):
    with pytest.raises(ValueError):
        await typed_recall.recall_typed_memory(
            tmp_config, "user-typed", topic="   ", query="  ",
        )


@pytest.mark.asyncio
async def test_unknown_memory_type_collapses_to_fact(tmp_config):
    """Unknown labels must NOT raise — they collapse to the default
    bucket so the filter stays predictable for a brain that fat-fingers
    a label."""
    await store.save_note(
        tmp_config, "user-typed",
        content="A neutral fact about chemistry.",
        title="Atomic weight",
    )
    hits = await typed_recall.query_by_memory_type(
        tmp_config, "user-typed", memory_type="nonsense-bucket",
    )
    titles = [h.get("title") for h in hits]
    assert "Atomic weight" in titles, (
        "unknown memory_type must coerce to DEFAULT_MEMORY_TYPE=fact, "
        "which matches a plain note saved without explicit tags"
    )


@pytest.mark.asyncio
async def test_memory_type_filter_returns_only_project(tmp_config):
    await store.save_note(
        tmp_config, "user-typed",
        content="A user preference fact.",
        title="LLM preference",
        tags=["user"],
    )
    hits = await typed_recall.query_by_memory_type(
        tmp_config, "user-typed", memory_type="project",
    )
    assert hits == []


@pytest.mark.asyncio
async def test_memory_type_filter_returns_only_specified(tmp_config):
    await store.save_note(
        tmp_config, "user-typed",
        content="user fact content",
        title="LLM-pref",
        tags=["user"],
    )
    hits = await typed_recall.query_by_memory_type(
        tmp_config, "user-typed", memory_type="project",
    )
    found_types = set()
    for h in hits:
        found_types.add(h.get("memory_type"))
    assert found_types == set()


async def _seed(config, user_id, samples):
    """Bulk-write a list of (content, title, tags) tuples."""
    saved = []
    for content, title, tags in samples:
        note = await store.save_note(
            config, user_id,
            content=content, title=title, tags=list(tags),
        )
        saved.append(note)
    return saved


@pytest.mark.asyncio
async def test_filter_returns_only_specified_type(tmp_config):
    seeds = [
        ("user content", "Profile note", ("user",)),
    ]
    await _seed(tmp_config, "user-typed", seeds)
    hits = await typed_recall.query_by_memory_type(
        tmp_config, "user-typed", memory_type="project",
    )
    assert hits == []


@pytest.mark.asyncio
async def test_filter_with_two_seeds(tmp_config):
    seeds = []
    seeds.append(("alpha body", "TitleA", ("user",)))
    seeds.append(("beta body", "TitleB", ("plan",)))
    await _seed(tmp_config, "user-typed", seeds)

    hits = await typed_recall.query_by_memory_type(
        tmp_config, "user-typed", memory_type="project",
    )
    types_seen = set()
    for h in hits:
        types_seen.add(h.get("memory_type"))
    assert types_seen == {"project"}


@pytest.mark.asyncio
async def test_topic_resolves_via_title_match(tmp_config):
    """A note whose title matches the topic surfaces via title_key
    resolution, irrespective of memory_type filter (it's the topic
    page itself)."""
    seeded = await _seed(
        tmp_config, "user-typed",
        [("project state for james blue", "James Blue", ("project",))],
    )
    hits = await typed_recall.recall_typed_memory(
        tmp_config, "user-typed",
        memory_type="project", topic="james blue",
    )
    ids = [h.get("id") for h in hits]
    assert seeded[0]["id"] in ids


@pytest.mark.asyncio
async def test_wikilink_crawl_surfaces_linking_notes(tmp_config):
    """When the topic has its own note AND a different note links to it
    via a [[wikilink]], the recall must return BOTH."""
    target = await store.save_note(
        tmp_config, "user-typed",
        content="The hub note for James Blue.",
        title="James Blue",
        tags=["project"],
    )
    linker = await store.save_note(
        tmp_config, "user-typed",
        content="Working session pointing at [[James Blue]] for context.",
        title="Session 2026-05-19 — James planning",
        tags=["session-log"],
    )
    hits = await typed_recall.recall_typed_memory(
        tmp_config, "user-typed", topic="James Blue",
    )
    ids = set()
    for h in hits:
        ids.add(h.get("id"))
    assert target["id"] in ids
    assert linker["id"] in ids, (
        "wikilink graph crawl must surface notes whose [[wikilink]] "
        "points at the topic"
    )


@pytest.mark.asyncio
async def test_limit_honored(tmp_config):
    """``limit`` caps the returned list. Saturate the type filter with
    more matches than the cap and confirm we return exactly ``limit``."""
    seeds = []
    for i in range(8):
        seeds.append((f"body {i}", f"Project card {i}", ("project",)))
    await _seed(tmp_config, "user-typed", seeds)
    hits = await typed_recall.query_by_memory_type(
        tmp_config, "user-typed", memory_type="project", limit=3,
    )
    assert len(hits) == 3


@pytest.mark.asyncio
async def test_returned_dict_shape(tmp_config):
    """Every hit carries the keys the brain needs: id, title, content,
    memory_type, importance, tags. (``_score`` only on semantic path.)"""
    await store.save_note(
        tmp_config, "user-typed",
        content="Hub for the James Blue contract.",
        title="James Blue",
        tags=["project"],
        importance=8,
    )
    hits = await typed_recall.recall_typed_memory(
        tmp_config, "user-typed", topic="James Blue",
    )
    assert len(hits) >= 1
    note = hits[0]
    for required in ("id", "title", "content", "memory_type",
                     "importance", "tags"):
        assert required in note, f"missing key in returned shape: {required}"
    assert note.get("memory_type") == "project"
    assert note.get("importance") == 8


@pytest.mark.asyncio
async def test_skill_rejects_empty_inputs(tmp_config):
    skill = RecallTypedMemorySkill(config=tmp_config)
    out = await skill.execute("user-typed", {})
    assert "no input" in out.lower()


@pytest.mark.asyncio
async def test_skill_renders_hits(tmp_config):
    await store.save_note(
        tmp_config, "user-typed",
        content="Hub for the James Blue contract.",
        title="James Blue",
        tags=["project"],
    )
    skill = RecallTypedMemorySkill(config=tmp_config)
    params = {}
    params["memory_type"] = "project"
    params["topic"] = "James Blue"
    out = await skill.execute("user-typed", params)
    assert "James Blue" in out
    assert "project" in out
