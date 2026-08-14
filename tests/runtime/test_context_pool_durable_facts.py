"""Context-pool churn: durable facts must not age out of the prompt.

``build_context`` fed its memory pool from
``lazybrain.store.list_notes(config, user_id, limit=40)`` — ordered
``pinned DESC, created_at DESC``. Since the note store is overwhelmingly
auto-captured noise (per-message captures, per-tool lesson cards,
per-URL visit notes), that 40-row window churned within hours and
durable user/project facts became permanently invisible to the cached
system prompt.

The call site now uses ``list_memory_notes`` (typed + importance-ordered).
These tests assert the assembled prompt, plus a negative control that
restores the old query and shows the fact disappearing again.

The second half covers the MEMORY_UNIFIED landmine from the pool's side:
with the flag on, a ``save_memory`` fact exists ONLY as a ``#memory``
LazyBrain mirror, so the pool must accept it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store as note_store
from lazyclaw.lazybrain.store import _content_aad, _title_aad
from lazyclaw.memory.personal import save_memory
from lazyclaw.runtime import context_builder

_USER_ID = "u-pool-churn"
_DURABLE = "DURABLE-FACT-MARKER user's timezone is Europe/Madrid"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "pool-churn", "x", "salt-pool-churn-test"),
        )
        await db.commit()
    _reset_context_builder_globals()
    try:
        yield c
    finally:
        _reset_context_builder_globals()
        await close_pool()


def _reset_context_builder_globals() -> None:
    """Clear the module-global capability caches AND rebind ``_cache_lock``.

    ``context_builder._cache_lock`` is created at import time and binds to
    the first event loop that awaits it. pytest-asyncio gives every test its
    own loop, so the second ``build_context`` in this module would otherwise
    die with "Lock is bound to a different event loop". Production runs one
    loop for the process lifetime, so this is a test-harness concern only.
    """
    context_builder.invalidate_capabilities_cache()
    context_builder._cache_lock = asyncio.Lock()


async def _insert_raw_note(
    cfg: Config,
    note_id: str,
    *,
    content: str,
    title: str,
    tags_json: str | None,
    importance: int,
    memory_type: str | None,
    created_at: str,
) -> None:
    dek = await get_user_dek(cfg, _USER_ID)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, importance, "
            "pinned, title_key, memory_type, archived, embedding_dirty, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 0, 0, ?, ?)",
            (
                note_id,
                _USER_ID,
                encrypt_field(title, dek, _title_aad(_USER_ID)),
                encrypt_field(content, dek, _content_aad(_USER_ID)),
                tags_json,
                importance,
                title.lower(),
                memory_type,
                created_at,
                created_at,
            ),
        )
        await db.commit()


async def _bury_under_noise(cfg: Config, count: int = 120) -> None:
    """``count`` newer auto-capture notes — auto-inject-typed too, so only
    importance + age separate them from the durable fact."""
    for i in range(count):
        await _insert_raw_note(
            cfg,
            f"noise-{i:03d}",
            content=f"visited example.com/{i} — auto-captured chatter",
            title=f"chatter {i}",
            tags_json='["auto"]',
            importance=5,
            memory_type="fact",
            created_at=f"2026-08-14 10:{i // 60:02d}:{i % 60:02d}.000000",
        )


# ─── Defect 2: the durable fact survives the churn ────────────────────────


async def test_old_high_importance_fact_is_injected_under_newer_noise(cfg):
    await _insert_raw_note(
        cfg,
        "note-durable",
        content=_DURABLE,
        title="User timezone",
        tags_json='["user"]',
        importance=9,
        memory_type="user",
        created_at="2026-01-01 09:00:00.000000",  # months older than the noise
    )
    await _bury_under_noise(cfg)

    prompt = await context_builder.build_context(cfg, _USER_ID)

    assert "## What I know about you" in prompt, (
        "memory section missing — the assertion below would pass trivially"
    )
    assert _DURABLE in prompt, (
        "durable user fact aged out of the context pool — the 40-note "
        "window is churning again"
    )


async def test_negative_control_old_newest_first_query_loses_the_fact(
    cfg, monkeypatch,
):
    """Restore the pre-fix query and the same fact vanishes. Without this
    the test above can't distinguish 'fixed' from 'never broken'."""
    await _insert_raw_note(
        cfg,
        "note-durable",
        content=_DURABLE,
        title="User timezone",
        tags_json='["user"]',
        importance=9,
        memory_type="user",
        created_at="2026-01-01 09:00:00.000000",
    )
    await _bury_under_noise(cfg)

    async def _legacy_newest_first(config, user_id, *, limit=40, **kwargs):
        return await note_store.list_notes(config, user_id, limit=limit)

    monkeypatch.setattr(note_store, "list_memory_notes", _legacy_newest_first)

    prompt = await context_builder.build_context(cfg, _USER_ID)
    assert _DURABLE not in prompt, (
        "the old newest-40 query still surfaced the buried fact — the "
        "churn scenario is no longer reproduced, so the positive test "
        "above proves nothing"
    )


async def test_session_log_notes_still_never_reach_the_pool(cfg):
    """The typed auto-inject gate is load-bearing (2026-05-19 hallucination
    class) — the new query must not widen it."""
    leak = "SESSION-LOG-LEAK-MARKER paraphrased channel dump"
    await _insert_raw_note(
        cfg,
        "note-sessionlog",
        content=leak,
        title="## Last Upwork Conversation",
        tags_json='["session-log"]',
        importance=9,
        memory_type="session-log",
        created_at="2026-08-14 12:00:00.000000",
    )
    prompt = await context_builder.build_context(cfg, _USER_ID)
    assert leak not in prompt


# ─── Defect 1: the pool under MEMORY_UNIFIED ──────────────────────────────


async def test_unified_mode_user_fact_reaches_the_context_pool(cfg):
    """Flag on → no legacy personal_memory row exists, so the ``#memory``
    LazyBrain mirror is the only path into the prompt."""
    cfg.memory_unified = True
    await save_memory(cfg, _USER_ID, _DURABLE, memory_type="fact", importance=8)
    await _bury_under_noise(cfg)

    prompt = await context_builder.build_context(cfg, _USER_ID)
    assert _DURABLE in prompt, (
        "user-saved fact unreachable under MEMORY_UNIFIED — the #memory "
        "tag exclusion is still filtering the only copy"
    )


async def test_dual_write_injects_the_fact_exactly_once(cfg):
    """Flag off → legacy row + mirror both exist. The mirror stays filtered
    so the prompt shows the fact once, not twice."""
    cfg.memory_unified = False
    await save_memory(cfg, _USER_ID, _DURABLE, memory_type="fact", importance=8)

    prompt = await context_builder.build_context(cfg, _USER_ID)
    assert prompt.count(_DURABLE) == 1, (
        f"expected one injected copy, got {prompt.count(_DURABLE)}"
    )
