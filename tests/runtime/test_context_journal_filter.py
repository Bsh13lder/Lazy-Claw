"""Tests for the inject-time journal + pinned-note cache filter.

Closes the contamination gap left by the 2026-05-20 typed-memory rollout:
the personal-memory pool was filtered via ``is_auto_inject_type`` but
the LazyBrain pinned + today's-journal injection at lines ~408–466 of
``lazyclaw/runtime/context_builder.py`` bypassed it entirely. The pure
helpers under test are in ``lazyclaw.runtime.context_journal_filter``;
the wiring through ``build_context`` is asserted at the end with a real
tmp DB.

Scenario the integration test enforces: a ``Journal — 2026-05-20`` note,
a ``user``-typed note, and a NULL-``memory_type`` pinned note. The
journal and the NULL-pinned must be absent from the assembled system
prompt; the ``user``-typed note must be present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import journal as lb_journal
from lazyclaw.lazybrain import store as lb_store
from lazyclaw.runtime import context_builder
from lazyclaw.runtime.context_journal_filter import (
    filter_pinned_for_cache,
    is_journal_title,
    should_inject_journal,
)


_USER_ID = "u-journal-filter"


# ─── Pure helpers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Journal — 2026-05-20", True),
        ("Journal — 1999-01-01", True),
        # Worker-LLM refreshed title shape used by ``_maybe_refresh_title``.
        ("2026-05-20 — Shipped F1 grounding, fixed canvas bug", True),
        # Surrounding whitespace must not defeat the match.
        ("   Journal — 2026-05-20   ", True),
        # Negatives — these MUST keep injecting normally.
        ("Notes about Journal — 2026-05-20 entry", False),
        ("My personal journal entry", False),
        ("Journal — not a date", False),
        ("2026-05-20", False),
        ("", False),
        (None, False),
    ],
)
def test_is_journal_title_matches_canonical_shapes(
    title: str | None, expected: bool,
) -> None:
    assert is_journal_title(title) is expected


def test_filter_pinned_drops_journal_titles_regardless_of_memory_type() -> None:
    """Defense-in-depth: even a pinned note whose ``memory_type`` is
    auto-inject-eligible (``user``) must be dropped if its title is the
    daily-journal shape. Closes a hypothetical regression where a future
    write path mis-tags a journal page."""
    pinned = [
        {"title": "Journal — 2026-05-20", "memory_type": "user",
         "content": "rolling diary content"},
        {"title": "Sonnet preference", "memory_type": "user",
         "content": "user prefers Sonnet"},
    ]
    kept, excluded = filter_pinned_for_cache(pinned)
    assert [n["title"] for n in kept] == ["Sonnet preference"]
    assert excluded == ["Journal — 2026-05-20"]


def test_filter_pinned_drops_null_memory_type_fail_closed() -> None:
    """Pre-backfill rows have ``memory_type IS NULL``. The cached layer
    must fail closed — a row we haven't classified is a row we don't
    trust to pre-inject."""
    pinned = [
        {"title": "Legacy pinned", "memory_type": None,
         "content": "pre-phase-1 content"},
        {"title": "Typed pinned", "memory_type": "reference",
         "content": "https://docs.example.com"},
    ]
    kept, excluded = filter_pinned_for_cache(pinned)
    assert [n["title"] for n in kept] == ["Typed pinned"]
    assert excluded == ["Legacy pinned"]


def test_filter_pinned_drops_session_log_and_fact_and_other() -> None:
    pinned = [
        {"title": "session recap", "memory_type": "session-log",
         "content": "x"},
        {"title": "random fact", "memory_type": "fact", "content": "x"},
        {"title": "uncategorised", "memory_type": "other", "content": "x"},
        {"title": "real rule", "memory_type": "feedback", "content": "x"},
    ]
    kept, _ = filter_pinned_for_cache(pinned)
    assert [n["title"] for n in kept] == ["real rule"]


def test_filter_pinned_never_mutates_input() -> None:
    """The coding-style rules require new-object returns. Confirm we
    don't accidentally pop / mutate the caller's list."""
    pinned = [
        {"title": "Journal — 2026-05-20", "memory_type": "user",
         "content": "x"},
        {"title": "ok", "memory_type": "user", "content": "y"},
    ]
    before = [dict(n) for n in pinned]
    kept, _ = filter_pinned_for_cache(pinned)
    assert pinned == before
    assert kept is not pinned


def test_filter_pinned_handles_empty_and_none() -> None:
    assert filter_pinned_for_cache([]) == ([], [])
    # Defensive: ``filter_pinned_for_cache(None)`` should not raise even
    # though the caller signature is ``list[dict]``.
    assert filter_pinned_for_cache(None) == ([], [])  # type: ignore[arg-type]


def test_should_inject_journal_excludes_canonical_titles() -> None:
    assert should_inject_journal(
        {"title": "Journal — 2026-05-20", "content": "bullets here"},
    ) is False
    assert should_inject_journal(
        {"title": "2026-05-20 — Shipped F1 grounding",
         "content": "refreshed bullets"},
    ) is False


def test_should_inject_journal_handles_missing_or_empty() -> None:
    assert should_inject_journal(None) is False
    assert should_inject_journal({}) is False
    assert should_inject_journal({"title": "ok", "content": ""}) is False
    assert should_inject_journal(
        {"title": "ok", "content": "   "},
    ) is False


def test_should_inject_journal_allows_non_journal_titles() -> None:
    """Defensive — the helper must not over-block. A note that happens
    to be returned by ``get_journal`` but has a non-journal title (a
    hypothetical migration artifact) is allowed through."""
    assert should_inject_journal(
        {"title": "Some other page", "content": "real content"},
    ) is True


# ─── Integration: build_context excludes the contamination shapes ─────────


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER_ID, "journal-filter", "x", "salt-journal-filter-test"),
        )
        await db.commit()
    # Caches are module-global — clear so a previous test can't leak
    # state into our prompt assertions.
    context_builder.invalidate_capabilities_cache()
    try:
        yield cfg
    finally:
        context_builder.invalidate_capabilities_cache()
        await close_pool()


@pytest.mark.asyncio
async def test_build_context_excludes_journal_and_null_pinned(
    tmp_config: Config,
) -> None:
    """End-to-end: a real journal note + a real user-typed note + a
    NULL-typed pinned note. After ``build_context`` runs:

      * the ``Journal — YYYY-MM-DD`` page is NOT in the cached system
        prompt (line excluded by ``should_inject_journal``);
      * the NULL-``memory_type`` pinned row is NOT in the prompt
        (fail-closed by ``filter_pinned_for_cache``);
      * the user-typed safe note IS present so we don't over-block.
    """
    # 1. Real daily journal — uses the production journal write path so
    #    the title is the canonical ``Journal — YYYY-MM-DD`` shape.
    journal_marker = "JOURNAL-CONTAMINATION-MARKER-XYZ"
    await lb_journal.append_journal(
        tmp_config, _USER_ID, content=journal_marker,
    )

    # 2. A pinned, NULL-``memory_type`` row (simulates a pre-backfill
    #    legacy pinned note). We write directly to the DB because the
    #    typed save_note path always assigns a non-NULL memory_type.
    from lazyclaw.crypto.encryption import encrypt_field
    from lazyclaw.crypto.key_manager import get_user_dek
    from lazyclaw.lazybrain.store import _content_aad, _title_aad

    null_marker = "NULL-PINNED-CONTAMINATION-MARKER-ABC"
    dek = await get_user_dek(tmp_config, _USER_ID)
    enc_title = encrypt_field("legacy pinned", dek, _title_aad(_USER_ID))
    enc_content = encrypt_field(null_marker, dek, _content_aad(_USER_ID))
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, "
            "importance, pinned, title_key, memory_type, "
            "embedding_dirty, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, "
            "datetime('now'), datetime('now'))",
            (
                "note-null-pinned", _USER_ID, enc_title, enc_content, None,
                5, 1, "legacy pinned",
            ),
        )
        await db.commit()

    # 3. A pinned, ``user``-typed note that MUST survive.
    safe_marker = "SAFE-USER-NOTE-MARKER-DEF"
    await lb_store.save_note(
        tmp_config, _USER_ID,
        content=safe_marker,
        title="Sonnet preference",
        tags=["user"],
        pinned=True,
    )

    prompt = await context_builder.build_context(
        tmp_config, _USER_ID,
    )

    # Journal contamination check — the cached layer must NOT contain
    # the journal body, the section header, or the journal page title
    # rendered as a pinned bullet. We deliberately do NOT assert against
    # the substring ``Journal — <date>`` alone: the wikilink reindexer
    # in ``store.save_note`` legitimately appends ``[[Journal — <date>]]``
    # to non-journal notes so that clicking the day node in the graph
    # reveals what happened — that wikilink is just a graph edge, not
    # a paraphrased dump, so it doesn't trigger the cache contamination
    # class. The load-bearing checks are:
    #   1. journal body content (bullets, paraphrases) is absent
    #   2. the LazyBrain "Today's journal" section header is absent
    #   3. no pinned-line shape (``- **Journal — <date>** — …``) leaks
    assert journal_marker not in prompt, (
        "Today's journal body leaked into the cached system prompt"
    )
    assert "### 📓 Today's journal" not in prompt, (
        "Today's-journal section header leaked into the cached prompt"
    )
    # The pinned-notes bullet format from context_builder:
    #     - **<title>** — <snippet>
    # If a journal page ever leaks into the pinned section, that line
    # would show up here. The wikilink-appended ``_[[Journal — date]]_``
    # in another note's content does NOT match this anchor.
    assert "- **Journal — " not in prompt, (
        "Today's journal leaked into the pinned-notes bullet list"
    )

    # NULL-``memory_type`` pinned row must also be excluded.
    assert null_marker not in prompt, (
        "NULL memory_type pinned row leaked into the cached system prompt"
    )
    assert "- **legacy pinned** —" not in prompt, (
        "NULL-typed pinned row leaked into the pinned-notes bullet list"
    )

    # And the safe user-typed note must be present so we know the test
    # actually exercised the pinned injection (otherwise the assertions
    # above would pass trivially).
    assert safe_marker in prompt, (
        "user-typed pinned note missing — pinned injection layer "
        "didn't fire; the contamination assertions above pass trivially"
    )
