"""User-correction lesson mirror dedup — same content must merge into
one note, not stack duplicates.

Before this fix, ``runtime.lesson_store.store_lesson`` called
``lb_store.save_note`` unconditionally on every correction. A user
saying the same thing twice produced two rows with identical titles,
polluting the LazyBrain graph. The fix mirrors the
``auto_capture._is_recent_duplicate`` pattern but goes further — it
calls ``update_note`` on the existing row so refinements over time
land on the same card.
"""

from __future__ import annotations

import asyncio

from lazyclaw.runtime import lesson_store as mod
from lazyclaw.runtime.lesson_extractor import Lesson


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeLBStore:
    def __init__(self):
        self.notes: list[dict] = []

    async def save_note(
        self, config, user_id, *, content, title, tags, importance, **kw,
    ):
        note = {
            "id": f"n{len(self.notes) + 1}",
            "content": content,
            "title": title,
            "tags": tags,
            "importance": importance,
            "user_id": user_id,
        }
        self.notes.append(note)
        return note

    async def find_by_title(self, config, user_id, title):
        # Title-case-insensitive lookup mirrors the real store's
        # title_key normalization. Most-recent-wins, matching the
        # production behavior.
        for n in reversed(self.notes):
            if n["title"].lower() == title.lower() and n["user_id"] == user_id:
                return dict(n)
        return None

    async def update_note(
        self, config, user_id, note_id, *,
        content=None, tags=None, importance=None, **kw,
    ):
        for n in self.notes:
            if n["id"] == note_id and n["user_id"] == user_id:
                if content is not None:
                    n["content"] = content
                if tags is not None:
                    n["tags"] = tags
                if importance is not None:
                    n["importance"] = importance
                return dict(n)
        return None


def _install_fake(monkeypatch) -> _FakeLBStore:
    store = _FakeLBStore()
    import lazyclaw.lazybrain.store as real_store
    import lazyclaw.lazybrain.events as real_events
    monkeypatch.setattr(real_store, "save_note", store.save_note)
    monkeypatch.setattr(real_store, "find_by_title", store.find_by_title)
    monkeypatch.setattr(real_store, "update_note", store.update_note)
    monkeypatch.setattr(
        real_events, "publish_note_saved", lambda *a, **kw: None,
    )

    # personal.save_memory writes to the personal_memory DB table; we
    # don't care about that path here so stub it out entirely.
    async def fake_save_memory(*a, **kw):
        return "mem-id-stub"

    monkeypatch.setattr(
        "lazyclaw.memory.personal.save_memory", fake_save_memory,
    )
    return store


def test_three_identical_lessons_produce_one_note(monkeypatch):
    """The classic dedup scenario — three identical corrections should
    leave the graph with exactly one card, not three."""
    store = _install_fake(monkeypatch)
    lesson = Lesson(
        content="Use vault_get for API keys, not memory recall.",
        lesson_type="preference",
        domain=None,
        importance=6,
    )
    for _ in range(3):
        _run(mod.store_lesson(config=None, user_id="u1", lesson=lesson))
    assert len(store.notes) == 1


def test_evolved_correction_updates_existing_note(monkeypatch):
    """When a user refines the SAME correction with more detail — same
    first 60 chars → same canonical title — the existing note's content
    + tags + importance update; no new row gets created.

    The contract is intentionally lenient: the first 60 chars are the
    dedup handle, so rewording the lesson tail (which is common when a
    user adds nuance) merges into the same card. Total rewrites land
    as a new card (covered by `test_different_content_does_not_dedup`).
    """
    store = _install_fake(monkeypatch)

    # base has to be ≥60 chars so the canonical title (content[:60])
    # is identical between first + refined. Below it, the two contents
    # only diverge in the tail beyond char 60 — exactly the case the
    # dedup is meant to catch.
    base = (
        "Use vault_get for retrieving stored API keys; the memory-recall "
        "layer never has key values"
    )
    assert len(base) >= 60
    first = Lesson(
        content=base + " (short note).",
        lesson_type="preference",
        domain=None,
        importance=5,
    )
    refined = Lesson(
        content=base + " — only the key NAME, never the value. Use vault.",
        lesson_type="preference",
        domain=None,
        importance=7,
    )
    _run(mod.store_lesson(config=None, user_id="u1", lesson=first))
    _run(mod.store_lesson(config=None, user_id="u1", lesson=refined))

    assert len(store.notes) == 1
    body = store.notes[0]["content"]
    assert "only the key NAME" in body
    assert store.notes[0]["importance"] == 7


def test_different_content_does_not_dedup(monkeypatch):
    """Truly different corrections should produce separate notes —
    the dedup must not be over-eager."""
    store = _install_fake(monkeypatch)

    a = Lesson(
        content="Use vault_get for API keys.",
        lesson_type="preference", domain=None, importance=5,
    )
    b = Lesson(
        content="Always cite sources in research replies.",
        lesson_type="preference", domain=None, importance=5,
    )
    _run(mod.store_lesson(config=None, user_id="u1", lesson=a))
    _run(mod.store_lesson(config=None, user_id="u1", lesson=b))

    assert len(store.notes) == 2


def test_site_lesson_skips_lazybrain_mirror(monkeypatch):
    """Site lessons go to ``site_memory.remember`` directly — they
    never hit the lazybrain mirror path. This guards against a
    refactor that accidentally moves the dedup check upstream of the
    site/preference split."""
    store = _install_fake(monkeypatch)

    # Stub out site_memory.remember so the test doesn't need a real DB.
    captured: list[dict] = []

    async def fake_remember(config, user_id, url, *, memory_type, title, content):
        captured.append({
            "user_id": user_id, "url": url, "title": title,
        })
        return "site-mem-id"

    monkeypatch.setattr(
        "lazyclaw.browser.site_memory.remember", fake_remember,
    )

    lesson = Lesson(
        content="Click 'Sign in with Google' button to skip CAPTCHA.",
        lesson_type="site",
        domain="example.com",
        importance=6,
    )
    _run(mod.store_lesson(
        config=None, user_id="u1", lesson=lesson, url="https://example.com/login",
    ))

    # Site lesson went to site_memory, NOT to lazybrain.
    assert len(captured) == 1
    assert len(store.notes) == 0
