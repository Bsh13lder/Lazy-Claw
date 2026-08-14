"""The MEMORY_UNIFIED landmine: ``#memory`` mirrors must stay retrievable.

``memory/personal.py:save_memory`` mirrors every user fact into LazyBrain
tagged ``#memory``. ``lazybrain/store.py`` excluded that tag from
``is_user_facing_memory_note`` — correct in dual-write mode (the legacy
``personal_memory`` row is already in the pool, and letting the mirror
through would double-hit), but a landmine under ``MEMORY_UNIFIED=1``:
``save_memory`` then skips the legacy INSERT, so the mirror is the ONLY
copy and every user-saved fact becomes unretrievable in BOTH the recall
note stream and the context pool.

The fix makes the ``memory`` exclusion conditional on the flag. These
tests pin both states end-to-end through the real write path.

The context-pool half of "flag on → retrievable everywhere" lives in
``tests/runtime/test_context_pool_durable_facts.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store as note_store
from lazyclaw.lazybrain.store import (
    is_user_facing_memory_note,
    memory_mirrors_are_authoritative,
)
from lazyclaw.memory.personal import get_memories, save_memory

# NOTE: no module-level ``pytest.mark.asyncio`` — this file mixes sync
# filter tests with async DB tests and pytest is in asyncio_mode="auto".

_USER_ID = "u-unified"
_FACT = "User's Google Workspace email is blckitteam@example.com"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "unified", "x", "salt-unified-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _mirror_note(cfg: Config) -> dict:
    """The ``#memory`` note ``save_memory`` wrote into LazyBrain."""
    notes = await note_store.list_notes(cfg, _USER_ID, limit=50)
    mirrors = [n for n in notes if "memory" in (n.get("tags") or [])]
    assert len(mirrors) == 1, f"expected exactly one mirror, got {len(mirrors)}"
    return mirrors[0]


# ─── The pure filter, both flag states ────────────────────────────────────


def test_filter_excludes_mirror_in_dual_write_mode():
    note = {"tags": ["memory", "auto", "owner/user", "kind/fact"],
            "title": "Fact: x", "content": _FACT}
    assert is_user_facing_memory_note(note, include_memory_mirrors=False) is False
    assert is_user_facing_memory_note(
        note, config=Config(memory_unified=False),
    ) is False


def test_filter_admits_mirror_in_unified_mode():
    note = {"tags": ["memory", "auto", "owner/user", "kind/fact"],
            "title": "Fact: x", "content": _FACT}
    assert is_user_facing_memory_note(note, include_memory_mirrors=True) is True
    assert is_user_facing_memory_note(
        note, config=Config(memory_unified=True),
    ) is True


def test_unified_mode_relaxes_only_the_memory_tag():
    """Every other exclusion family must survive the flag flip."""
    unified = Config(memory_unified=True)
    for tags in (["daily-log"], ["session-end"], ["kind/shape"], ["kind/legacy"]):
        assert is_user_facing_memory_note(
            {"tags": tags, "title": "t", "content": "c"}, config=unified,
        ) is False
    for title in ("Journal — 2026-08-14", "Daily summary 2026-08-14",
                  "Weekly summary W33"):
        assert is_user_facing_memory_note(
            {"tags": [], "title": title, "content": "c"}, config=unified,
        ) is False
    # And a genuine note still passes.
    assert is_user_facing_memory_note(
        {"tags": ["user"], "title": "Preference", "content": "c"},
        config=unified,
    ) is True


def test_flag_resolution_prefers_config_then_env(monkeypatch):
    assert memory_mirrors_are_authoritative(Config(memory_unified=True)) is True
    assert memory_mirrors_are_authoritative(Config(memory_unified=False)) is False
    # No config in hand (e.g. runtime/self_recall.py) → env fallback, same
    # var load_config() reads.
    monkeypatch.delenv("MEMORY_UNIFIED", raising=False)
    assert memory_mirrors_are_authoritative() is False
    monkeypatch.setenv("MEMORY_UNIFIED", "1")
    assert memory_mirrors_are_authoritative() is True
    monkeypatch.setenv("MEMORY_UNIFIED", "0")
    assert memory_mirrors_are_authoritative() is False


def test_default_call_shape_is_unchanged():
    """Existing callers pass a note and nothing else — that must keep
    meaning 'dual-write behaviour' whenever the flag is off."""
    note = {"tags": ["memory"], "title": "Fact: x", "content": _FACT}
    assert is_user_facing_memory_note(note) is False


# ─── End-to-end through save_memory ───────────────────────────────────────


async def test_dual_write_keeps_legacy_row_and_hides_the_mirror(cfg):
    cfg.memory_unified = False
    await save_memory(cfg, _USER_ID, _FACT, memory_type="fact", importance=7)

    legacy = await get_memories(cfg, _USER_ID)
    assert [m["content"] for m in legacy] == [_FACT], (
        "dual-write mode must still write the legacy personal_memory row"
    )

    mirror = await _mirror_note(cfg)
    assert is_user_facing_memory_note(mirror, config=cfg) is False, (
        "the mirror must stay filtered while the legacy row covers the fact"
    )

    # Note stream (recall's LazyBrain lane) → mirror suppressed, so the
    # fact surfaces exactly once (via personal_memory), never twice.
    from lazyclaw.skills.builtin.memory_recall import _safe_recent_lb_notes

    assert await _safe_recent_lb_notes(cfg, _USER_ID, limit=8) == []


async def test_unified_mode_skips_legacy_row_and_exposes_the_mirror(cfg):
    cfg.memory_unified = True
    memory_id = await save_memory(
        cfg, _USER_ID, _FACT, memory_type="fact", importance=7,
    )
    assert memory_id.startswith("lb:")

    assert await get_memories(cfg, _USER_ID) == [], (
        "unified mode must NOT write the legacy personal_memory row — if it "
        "does, this test is no longer exercising the landmine"
    )

    mirror = await _mirror_note(cfg)
    assert is_user_facing_memory_note(mirror, config=cfg) is True, (
        "under MEMORY_UNIFIED the mirror is the ONLY copy of the fact"
    )

    from lazyclaw.skills.builtin.memory_recall import _safe_recent_lb_notes

    stream = await _safe_recent_lb_notes(cfg, _USER_ID, limit=8)
    assert [n["content"] for n in stream] == [_FACT]


async def test_unified_mode_mirror_is_an_auto_inject_candidate(cfg):
    """The context pool's dedicated query must see it too."""
    cfg.memory_unified = True
    await save_memory(cfg, _USER_ID, _FACT, memory_type="fact", importance=7)

    candidates = await note_store.list_memory_notes(cfg, _USER_ID, limit=40)
    assert [n["content"] for n in candidates] == [_FACT]

    cfg.memory_unified = False
    assert await note_store.list_memory_notes(cfg, _USER_ID, limit=40) == []


# ─── recall_memories fan-out: one hit, not two ────────────────────────────


def _result_rows(rendered: str) -> int:
    """Count emitted hit rows — the formatter writes one ``- [src:conf …]``
    bullet per merged result (title AND body echo the content, so a naive
    substring count double-reports a single row)."""
    return sum(1 for line in rendered.splitlines() if line.startswith("- ["))


def _stub_semantic(monkeypatch, notes: list[dict]) -> None:
    """Force the LazyBrain semantic lane to return ``notes`` (no Ollama)."""
    from lazyclaw.lazybrain import embeddings as lb_embeddings

    async def _fake_semantic_search(config, user_id, query, **kwargs):
        if kwargs.get("tag_prefix") == "kind/shape":
            return {"results": []}
        return {"results": notes}

    monkeypatch.setattr(
        lb_embeddings, "semantic_search", _fake_semantic_search,
    )


async def test_recall_returns_one_row_per_fact_in_dual_write(cfg, monkeypatch):
    """The mirror is dropped by the filter; the legacy row answers. Exactly
    one row mentions the fact — no double-hit."""
    cfg.memory_unified = False
    await save_memory(cfg, _USER_ID, _FACT, memory_type="fact", importance=7)
    _stub_semantic(monkeypatch, [await _mirror_note(cfg)])

    from lazyclaw.skills.builtin.memory_recall import MemoryRecallSkill

    out = await MemoryRecallSkill(config=cfg).execute(
        _USER_ID, {"query": "Google Workspace email"},
    )
    assert _result_rows(out) == 1, out
    assert "blckitteam@example.com" in out
    assert "[memory:" in out, "the surviving row must be the legacy fact"


async def test_recall_finds_the_fact_in_unified_mode(cfg, monkeypatch):
    """With no legacy row at all, the mirror MUST answer — this is the
    query that returned nothing before the fix."""
    cfg.memory_unified = True
    await save_memory(cfg, _USER_ID, _FACT, memory_type="fact", importance=7)
    _stub_semantic(monkeypatch, [await _mirror_note(cfg)])

    from lazyclaw.skills.builtin.memory_recall import MemoryRecallSkill

    out = await MemoryRecallSkill(config=cfg).execute(
        _USER_ID, {"query": "Google Workspace email"},
    )
    assert _result_rows(out) == 1, out
    assert "blckitteam@example.com" in out
    assert "[note:" in out, "the surviving row must be the LazyBrain mirror"


async def test_recall_still_hides_the_mirror_in_dual_write(cfg, monkeypatch):
    """Negative control for the test above: same stubbed semantic hit, flag
    off → the note lane contributes nothing and only the legacy row shows."""
    cfg.memory_unified = False
    await save_memory(cfg, _USER_ID, _FACT, memory_type="fact", importance=7)
    _stub_semantic(monkeypatch, [await _mirror_note(cfg)])

    from lazyclaw.skills.builtin.memory_recall import (
        _safe_lazybrain_combined_search,
    )

    notes, _lessons = await _safe_lazybrain_combined_search(
        cfg, _USER_ID, "Google Workspace email",
    )
    assert notes == []
