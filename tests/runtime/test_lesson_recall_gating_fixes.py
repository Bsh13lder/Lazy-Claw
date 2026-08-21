"""Lesson recall gating fixes (2026-08-21 learning-machinery audit).

Measured: specialist-lesson recall missed 11/11 — STRUCTURAL: it queried
``topic=browser_specialist`` but writers only ever produce base topics
(``browser``, ``email``, ...). And the context builder paid an Ollama
semantic search (~100-600 ms pre-LLM) on most turns with a 44% total
miss rate, because broad keyword triggers query topics that hold no
cards. The per-message verification pump added a non-indexed LIKE table
scan on every user message.

Fixes: specialist recall maps to the base topic; context-builder recall
is gated on a cached topics-with-cards set; the pump runs every Nth
turn instead of every message.
"""

from __future__ import annotations

import inspect

from lazyclaw.runtime.specialist_lessons import _base_topic


def test_specialist_names_map_to_their_base_topic() -> None:
    assert _base_topic("browser_specialist") == "browser"
    assert _base_topic("email_specialist") == "email"
    assert _base_topic("web_research_specialist") == "web_research"


def test_non_suffixed_names_pass_through() -> None:
    assert _base_topic("explore") == "explore"
    assert _base_topic("general_purpose") == "general_purpose"


def test_specialist_recall_queries_the_base_topic() -> None:
    from lazyclaw.runtime import specialist_lessons as mod

    src = inspect.getsource(mod.recall_specialist_lessons)
    assert "_base_topic(" in src, (
        "querying topic=<name>_specialist can never match — writers only "
        "produce base topics (11/11 miss in prod)"
    )


def test_topics_with_lessons_cache_exists_and_is_ttl_bounded() -> None:
    from lazyclaw.runtime import skill_lesson as sl

    assert hasattr(sl, "topics_with_lessons")
    src = inspect.getsource(sl.topics_with_lessons)
    assert "_TOPIC_CACHE" in src
    # The fetch is separable so tests and the TTL path can stub it.
    assert hasattr(sl, "_fetch_lesson_topics")


def test_topics_cache_avoids_refetch_within_ttl(monkeypatch) -> None:
    import asyncio

    from lazyclaw.runtime import skill_lesson as sl

    calls = {"n": 0}

    async def _fake_fetch(config, user_id):
        calls["n"] += 1
        return {"browser", "email"}

    monkeypatch.setattr(sl, "_fetch_lesson_topics", _fake_fetch)
    sl._TOPIC_CACHE.clear()

    async def run():
        a = await sl.topics_with_lessons(None, "u1")
        b = await sl.topics_with_lessons(None, "u1")
        return a, b

    a, b = asyncio.run(run())
    assert a == {"browser", "email"} == b
    assert calls["n"] == 1, "second call within TTL must hit the cache"


def test_context_builder_gates_recall_on_topics_with_cards() -> None:
    from lazyclaw.runtime import context_builder as cb

    src = inspect.getsource(cb._build_topic_lessons_section)
    assert "topics_with_lessons" in src, (
        "keyword-matched topics with no cards must not pay a semantic "
        "search (44% of recalls returned nothing)"
    )


def test_verification_pump_runs_every_nth_turn() -> None:
    from lazyclaw.runtime import agent as agent_mod

    src = inspect.getsource(agent_mod)
    idx = src.index("run_verification_pump")
    window = src[idx - 1500:idx + 300]
    assert "_VERIFY_PUMP_EVERY" in window, (
        "the pump's LIKE table scan must not run on every user message"
    )
