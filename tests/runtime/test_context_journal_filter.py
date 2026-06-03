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
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.runtime.context_journal_filter import (
    STALE_TOOL_ERROR_MARKER,
    filter_pinned_for_cache,
    is_journal_title,
    is_stale_provider_error,
    neutralize_stale_errors,
    quarantine_polluted_history,
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


def test_filter_pinned_drops_session_log_and_other_but_keeps_fact() -> None:
    """'fact' is now in AUTO_INJECT_TYPES — it is kept by the pinned filter.
    Only 'session-log', 'other', and NULL are excluded.
    """
    pinned = [
        {"title": "session recap", "memory_type": "session-log",
         "content": "x"},
        {"title": "random fact", "memory_type": "fact", "content": "x"},
        {"title": "uncategorised", "memory_type": "other", "content": "x"},
        {"title": "real rule", "memory_type": "feedback", "content": "x"},
    ]
    kept, _ = filter_pinned_for_cache(pinned)
    assert [n["title"] for n in kept] == ["random fact", "real rule"]


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


# ─── quarantine_polluted_history ──────────────────────────────────────────
#
# Closes the durable defense against the 2026-05-22 10:39 mimic cycle:
# yesterday's persisted assistant reply ("**James Blue (9:12 PM):** Mac
# [[computer]]") was loaded as history → the brain mimicked the wikilink
# leak → F1 phase-2 fired → retry produced the same content → degraded-
# accept shipped → next turn the cycle repeats. The in-memory history
# quarantine breaks the loop without mutating the DB.

from lazyclaw.llm.providers.base import LLMMessage  # noqa: E402


def _assistant(content: str) -> LLMMessage:
    return LLMMessage(role="assistant", content=content)


def _user(content: str) -> LLMMessage:
    return LLMMessage(role="user", content=content)


def _tool(content: str, tool_call_id: str = "tc-1") -> LLMMessage:
    return LLMMessage(role="tool", content=content, tool_call_id=tool_call_id)


def test_quarantines_assistant_with_wikilink_in_quote() -> None:
    """The load-bearing case: a bold-sender quote line containing a
    ``[[wikilink]]`` token is the deterministic memory-leak signature.
    The assistant message must be replaced with a quarantine stub."""
    polluted = (
        "Here is what James said:\n\n"
        "**James Blue (9:12 PM):** I have a Mac [[computer]] and iPad\n\n"
        "Let me know if you need more."
    )
    messages = [
        _user("check upwork"),
        _assistant(polluted),
    ]

    filtered = quarantine_polluted_history(messages)

    assert filtered[0].role == "user"
    assert filtered[0].content == "check upwork"
    assert filtered[1].role == "assistant"
    assert "[QUARANTINED" in filtered[1].content
    assert "[[computer]]" not in filtered[1].content
    assert "James Blue" not in filtered[1].content


def test_does_not_quarantine_clean_user_or_tool_messages() -> None:
    """Only ``role='assistant'`` is eligible. A user message that
    contains ``[[wikilink]]`` (the user typed it themselves) and a
    tool result that contains a polluted assistant draft (echo) must
    both pass through unchanged."""
    messages = [
        _user("show me my note about [[james blue contract]]"),
        _tool("**James (9:12 PM):** Mac [[computer]]"),
        _assistant("Reading your notes now."),
    ]

    filtered = quarantine_polluted_history(messages)

    assert filtered[0].content == "show me my note about [[james blue contract]]"
    assert filtered[1].content == "**James (9:12 PM):** Mac [[computer]]"
    # Clean assistant text — no quarantine.
    assert "[QUARANTINED" not in filtered[2].content
    assert filtered[2].content == "Reading your notes now."


def test_quarantine_replaces_content_keeps_role() -> None:
    """The quarantined replacement must preserve ``role`` and message
    type. Downstream code (tool_call pairing, provider serializers)
    keys off these fields, so a quarantined row that morphed into
    ``role='system'`` would corrupt the conversation shape."""
    polluted = (
        "> James (9:12 PM): my budget is [[budget_2026]]"
    )
    messages = [_assistant(polluted)]

    filtered = quarantine_polluted_history(messages)

    # Same class, same role, content swapped.
    assert isinstance(filtered[0], LLMMessage)
    assert filtered[0].role == "assistant"
    assert "[QUARANTINED" in filtered[0].content
    assert filtered[0].tool_calls is None


def test_quarantine_only_scans_last_N_messages() -> None:
    """Performance guard: messages older than the trailing scan window
    must NOT be inspected (the compressor has already collapsed them
    into a summary anyway). Build a 30-message history where ONLY the
    oldest assistant row is polluted — it must NOT be quarantined."""
    from lazyclaw.runtime.context_journal_filter import _QUARANTINE_SCAN_LIMIT

    # Polluted at index 0 — way outside the scan window.
    polluted_old = "**Bob (1:00 PM):** Hello [[old_note]]"
    # Clean assistants for the rest, plus user turns to look realistic.
    history: list[LLMMessage] = [_assistant(polluted_old)]
    # Pad with (_QUARANTINE_SCAN_LIMIT + 5) clean messages so the
    # polluted message ends up before the window starts. The scan
    # only inspects the last _QUARANTINE_SCAN_LIMIT messages.
    pad = _QUARANTINE_SCAN_LIMIT + 5
    for i in range(pad):
        history.append(_user(f"msg {i}"))
        history.append(_assistant(f"clean reply {i}"))

    filtered = quarantine_polluted_history(history)

    # The old polluted message MUST survive untouched — out of scan range.
    assert filtered[0].content == polluted_old
    # And clean trailing messages must also survive untouched.
    assert filtered[-1].content.startswith("clean reply")


def test_quarantine_handles_empty_or_none_content() -> None:
    """Defensive: empty list, empty content, and None-content rows
    must not raise. Pollution scan returns ``None`` for blanks."""
    # Empty list
    assert quarantine_polluted_history([]) == []
    # Empty content
    msg_empty = _assistant("")
    out = quarantine_polluted_history([msg_empty])
    assert out[0].content == ""

    # None content — has to roundtrip without crashing.
    msg_none = LLMMessage(role="assistant", content="")  # type: ignore
    # Force-override content to None via __dict__ so we test the
    # defensive ``or ""`` in the scanner.
    msg_none.content = None  # type: ignore[assignment]
    out2 = quarantine_polluted_history([msg_none])
    # Either passed through with None content, OR got coerced — both
    # are acceptable; the crucial assertion is "no raise".
    assert out2[0].role == "assistant"


def test_quarantine_does_not_mutate_input() -> None:
    """Coding-style rule: immutable returns. The caller's list AND
    the original message objects must not be mutated."""
    polluted = "**Alice (10:00 AM):** test [[leak]]"
    original = _assistant(polluted)
    messages = [original]
    snapshot_content = original.content

    out = quarantine_polluted_history(messages)

    assert original.content == snapshot_content  # original object intact
    assert messages[0] is original                # input list intact
    assert out is not messages                    # new list returned
    assert "[QUARANTINED" in out[0].content       # quarantine applied to copy


def test_quarantine_quote_mismatch_when_tool_results_provided() -> None:
    """When ``tool_results_this_turn`` is given AND the assistant
    quotes don't appear in the tool data, quarantine fires. This is
    the second signal (in addition to wikilink)."""
    # Brain invented quotes that nowhere appear in the live tool data.
    polluted = (
        "Here's what they said:\n"
        "> James (9:12 PM): I will pay $500 for this job\n"
        "> James (9:14 PM): Deadline is next Monday\n"
        "> James (9:15 PM): Send me your wallet address"
    )
    # Live tool result contains entirely different content — so all
    # three quotes are unverified (majority => quarantine).
    fresh_tool_results = [
        '[{"sender": "James", "content": "Hello, are you available?"}]',
    ]

    messages = [_assistant(polluted)]
    filtered = quarantine_polluted_history(
        messages, tool_results_this_turn=fresh_tool_results,
    )
    assert "[QUARANTINED" in filtered[0].content


def test_quarantine_quote_match_keeps_assistant_clean() -> None:
    """Negative companion to the previous test — when quotes DO
    appear in the tool data, the assistant message is NOT quarantined."""
    safe_reply = (
        "Here's what James said:\n"
        "> James (9:12 PM): Hello, are you available for this job?"
    )
    fresh_tool_results = [
        '[{"sender": "James", "content": "Hello, are you available for this job?"}]',
    ]
    messages = [_assistant(safe_reply)]
    filtered = quarantine_polluted_history(
        messages, tool_results_this_turn=fresh_tool_results,
    )
    assert "[QUARANTINED" not in filtered[0].content
    assert filtered[0].content == safe_reply


# ─── neutralize_stale_errors (stale provider/credit/auth/rate-limit) ──────

_CREDIT_ERR = (
    "Could not draft proposal: Error code: 400 - {'type': 'error', 'error': "
    "{'message': 'Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing.'}, 'request_id': 'req_011CbgSMgSgBV5JeQhSBHgLp'}"
)


def test_is_stale_provider_error_matches_and_misses():
    assert is_stale_provider_error(_CREDIT_ERR)
    assert is_stale_provider_error("Error code: 503 upstream down")
    assert is_stale_provider_error("rate_limit_error: slow down")
    assert is_stale_provider_error("credit balance too low")
    # Benign content must NOT match.
    assert not is_stale_provider_error("No file matched that path.")
    assert not is_stale_provider_error('{"unread": 2, "rooms": []}')
    assert not is_stale_provider_error("")
    assert not is_stale_provider_error(None)


def test_neutralize_tool_credit_error_preserves_pairing():
    msgs = [
        LLMMessage(role="user", content="apply 1"),
        LLMMessage(role="tool", content=_CREDIT_ERR, tool_call_id="call_1"),
    ]
    out = neutralize_stale_errors(msgs)
    assert len(out) == 2  # replace-not-drop
    tool = out[1]
    assert tool.role == "tool"
    assert tool.tool_call_id == "call_1"  # pairing preserved
    assert tool.content == STALE_TOOL_ERROR_MARKER
    assert "credit balance" not in tool.content
    assert "req_011" not in tool.content


def test_neutralize_assistant_blocker_recitation():
    msgs = [
        LLMMessage(
            role="assistant",
            content="🔴 Blocker still active: credit balance too low — top up at Plans & Billing.",
        ),
    ]
    out = neutralize_stale_errors(msgs)
    assert out[0].content == STALE_TOOL_ERROR_MARKER


def test_neutralize_leaves_benign_messages_untouched():
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="all good"),
        LLMMessage(role="tool", content="No file matched.", tool_call_id="c2"),
        LLMMessage(role="tool", content='{"unread": 2}', tool_call_id="c3"),
    ]
    out = neutralize_stale_errors(msgs)
    assert [m.content for m in out] == [
        "hi", "all good", "No file matched.", '{"unread": 2}',
    ]


def test_neutralize_does_not_disturb_wikilink_quarantine():
    """The two filters are independent — a wikilink-confab assistant row is
    NOT touched by the stale-error neutralizer (no provider-error tokens)."""
    confab = "> James Blue (9:12 PM): I have a Mac [[computer]]."
    out = neutralize_stale_errors([LLMMessage(role="assistant", content=confab)])
    assert out[0].content == confab  # untouched here; quarantine handles it
