"""Upsert stability — same (topic, action, intent) triple → one note row,
no matter how many times it's replayed with varying params/snippets.

Catches drift in ``_intent_slug`` or ``_canonical_title`` that would
silently re-enable the duplicate flood we cleaned up in v2. The DB
audit on 2026-05-13 found 100+ pre-v2 duplicate lesson cards under
titles like ``lesson (web/success): web_search: 5`` × 15 — those were
legacy rows whose title_key never got rewritten. New writes go through
``Skill shape · {topic}/{action} · {slug}`` and MUST upsert.
"""

from __future__ import annotations

import asyncio

from lazyclaw.runtime import skill_lesson as mod

# Reuse the existing fake-LB scaffold so we don't double-maintain it.
from tests.test_skill_lesson import _install_fake_lb, _run  # noqa: E402


def test_five_identical_calls_one_note(monkeypatch):
    """Five save_skill_lesson() calls with the same (topic, action, intent)
    triple — varying only params/result_snippet — must yield exactly one
    note row. Run log accumulates each replay."""
    from lazyclaw.lazybrain.store import _title_key

    canonical = mod._canonical_title("web", "web_search", "search-flights")
    key = _title_key(canonical)
    store, _ = _install_fake_lb(
        monkeypatch, existing_id_for_key={key: "n1"},
    )

    # Seed the existing card so the upsert path is the one under test.
    _run(store.save_note(
        config=None, user_id="u1", content="**Topic:** web\n",
        title=canonical,
        tags=["kind/shape", "topic/web", "action/web_search", "intent/search-flights"],
        importance=4,
        frontmatter={
            "kind": "shape", "topic": "web", "action": "web_search",
            "intent": "Search flights", "outcome": "pending",
            "replay_count": 1,
        },
    ))

    # Five replays with different params + result snippets — should all
    # merge into the same card.
    for i in range(5):
        _run(mod.save_skill_lesson(
            config=None, user_id="u1",
            topic="web", action="web_search",
            intent="Search flights",
            params={"query": f"flight to madrid {i}", "limit": i + 1},
            result_snippet=f"replay {i}: 12 results returned",
            outcome="success",
        ))

    # One and only one row.
    assert len(store.notes) == 1

    body = store.notes[0]["content"]
    from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    props, _, _ = parse_frontmatter(body)

    # replay_count climbed from the seed value (1) through the 5 replays.
    assert props["replay_count"] >= 6, (
        f"replay_count should be at least 6 after 5 replays on top of seed=1, "
        f"got {props['replay_count']}"
    )

    # Run log carries multiple entries — proves merge, not no-op.
    assert "## Run log" in body
    run_log_section = body.split("## Run log", 1)[1]
    bullet_lines = [line for line in run_log_section.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) >= 5, (
        f"expected ≥5 run-log bullets, got {len(bullet_lines)}: {bullet_lines}"
    )


def test_varying_params_dont_break_upsert(monkeypatch):
    """Title generator must ignore ``params`` and ``result_snippet`` —
    only (topic, action, intent_slug) participate in the title. If a
    future refactor accidentally inlines params into the title, this
    test catches it."""
    from lazyclaw.lazybrain.store import _title_key

    canonical = mod._canonical_title("mcp", "mcp_scraper_extract", "extract-entities")
    key = _title_key(canonical)
    store, _ = _install_fake_lb(
        monkeypatch, existing_id_for_key={key: "n1"},
    )

    _run(store.save_note(
        config=None, user_id="u1", content="**Topic:** mcp\n",
        title=canonical,
        tags=["kind/shape", "topic/mcp"],
        importance=4,
        frontmatter={
            "kind": "shape", "topic": "mcp",
            "action": "mcp_scraper_extract",
            "intent": "Extract entities", "outcome": "pending",
            "replay_count": 1,
        },
    ))

    # Wildly different params each call.
    for params in [
        {"url": "https://a.com"},
        {"url": "https://b.com", "schema": {"x": "y"}},
        {"url": "https://c.com", "depth": 3},
    ]:
        _run(mod.save_skill_lesson(
            config=None, user_id="u1",
            topic="mcp", action="mcp_scraper_extract",
            intent="Extract entities",
            params=params,
            outcome="success",
        ))

    assert len(store.notes) == 1


def test_canonical_title_is_deterministic():
    """Same triple in → same title out. Defends the upsert contract at
    the title-generator level, no DB needed."""
    a = mod._canonical_title("web", "web_search", "search-flights")
    b = mod._canonical_title("web", "web_search", "search-flights")
    assert a == b
    # Splitting the action on `:` is the documented normalization — make
    # sure variants of the same root action collapse to one title.
    c = mod._canonical_title("web", "web_search:google", "search-flights")
    assert a == c, (
        f"action variants should normalize to the same title: {a!r} vs {c!r}"
    )


def test_intent_slug_is_stable_across_phrasing():
    """``_intent_slug`` is the input to the upsert key — it must be
    deterministic for the same prompt and stable across surface drift
    like trailing punctuation, mixed case, or extra spaces.
    """
    base = mod._intent_slug("Create Google Sheet")
    assert base != ""
    assert mod._intent_slug("create google sheet") == base
    assert mod._intent_slug("  Create  Google  Sheet  ") == base
    # Empty / whitespace-only collapses to "" — caller substitutes
    # "no-intent" in `_canonical_title`, which is the documented
    # fallback (see skill_lesson.py:176).
    assert mod._intent_slug("") == ""
    assert mod._intent_slug("   ") == ""
