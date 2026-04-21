"""Pillar B tests — cross-topic skill-outcome lesson store.

Pins these invariants landed on 2026-04-21:

  * `_redact` drops sensitive keys at every nesting depth + truncates
    long strings (never leaks secrets into a PKM note).
  * `save_skill_lesson` writes a LazyBrain note with the canonical tag
    set (`lesson, auto, owner/agent, topic/<t>, outcome/<o>, action/<a>,
    intent/<slug>`) and a body our own parser can round-trip.
  * `save_skill_lesson` silently skips topics outside `LEARNING_TOPICS`
    so a typo in a caller can't pollute recall.
  * `recall_skill_lessons` filters by outcome and returns body-parsed
    fields (intent/action/outcome/params) in a shape the formatter
    turns into a prompt block ≤ 2 KB.
"""

from __future__ import annotations

import asyncio

from lazyclaw.runtime import skill_lesson as mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Redaction ────────────────────────────────────────────────────────


def test_redact_drops_sensitive_keys_at_every_depth():
    dirty = {
        "api_key": "sk-live-abc",
        "user": "me",
        "nested": {
            "password": "hunter2",
            "ok": "yes",
            "deeper": {"Authorization": "Bearer xyz"},
        },
        "list": [
            {"token": "abc", "visible": True},
            "plain-string",
        ],
    }
    clean = mod._redact(dirty)
    assert clean["api_key"] == "[redacted]"
    assert clean["user"] == "me"
    assert clean["nested"]["password"] == "[redacted]"
    assert clean["nested"]["ok"] == "yes"
    assert clean["nested"]["deeper"]["Authorization"] == "[redacted]"
    assert clean["list"][0]["token"] == "[redacted]"
    assert clean["list"][0]["visible"] is True
    assert clean["list"][1] == "plain-string"


def test_redact_truncates_long_strings():
    big = "x" * 500
    clean = mod._redact({"body": big})
    assert len(clean["body"]) <= mod._STRING_REDACTION_LIMIT + 5  # +ellipsis
    assert clean["body"].endswith("…")


def test_redact_passes_through_scalars():
    assert mod._redact(42) == 42
    assert mod._redact(3.14) == 3.14
    assert mod._redact(True) is True
    assert mod._redact(None) is None


def test_intent_slug_dashes_first_three_words():
    assert mod._intent_slug("Create Google Sheet named Hirossa") == "create-google-sheet"
    assert mod._intent_slug("   ") == ""
    assert mod._intent_slug("") == ""


# ── Save side ───────────────────────────────────────────────────────


class _FakeLBStore:
    def __init__(self):
        self.notes: list[dict] = []

    async def save_note(self, config, user_id, *, content, title, tags, importance, **kw):
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


class _FakeLBEvents:
    def __init__(self):
        self.published: list = []

    def publish_note_saved(self, *a, **kw):
        self.published.append((a, kw))


def _install_fake_lb(monkeypatch):
    """Replace `save_note` + `publish_note_saved` in-place.

    `save_skill_lesson` uses `from lazyclaw.lazybrain import store as lb_store`,
    which binds to the parent package's *attribute* — swapping sys.modules
    entries isn't enough once the real module has already been imported by
    another test. monkeypatch.setattr on the live functions is reliable."""
    store = _FakeLBStore()
    events = _FakeLBEvents()
    # Import so attributes exist on the parent package, then swap them.
    import lazyclaw.lazybrain.events  # noqa: F401
    import lazyclaw.lazybrain.store   # noqa: F401
    monkeypatch.setattr("lazyclaw.lazybrain.store.save_note", store.save_note)
    monkeypatch.setattr(
        "lazyclaw.lazybrain.events.publish_note_saved",
        events.publish_note_saved,
    )
    return store, events


def test_save_skill_lesson_writes_canonical_tags(monkeypatch):
    store, events = _install_fake_lb(monkeypatch)
    note_id = _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="n8n",
        action="n8n_create_workflow:spreadsheet.create",
        intent="Create Google Sheet named hirossa keyword research",
        params={"resource": "spreadsheet", "operation": "create",
                "title": "hirossa keyword research"},
        outcome="success",
    ))
    assert note_id == "n1"
    assert len(store.notes) == 1
    note = store.notes[0]
    tags = set(note["tags"])
    assert "lesson" in tags
    assert "auto" in tags
    assert "owner/agent" in tags
    assert "topic/n8n" in tags
    assert "outcome/success" in tags
    assert "action/n8n_create_workflow" in tags  # sub-op stripped from action tag
    assert any(t.startswith("intent/create-google-sheet") for t in tags)
    # Title carries topic + outcome + truncated intent.
    assert note["title"].startswith("Lesson (n8n/success):")
    # Body contains the full canonical shape.
    assert "**Topic:** n8n" in note["content"]
    assert "**Outcome:** success" in note["content"]
    assert "hirossa keyword research" in note["content"]
    # Event was published.
    assert len(events.published) == 1


def test_save_skill_lesson_redacts_secrets_in_params(monkeypatch):
    store, _ = _install_fake_lb(monkeypatch)
    _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="email",
        action="email_send",
        intent="Send notification email",
        params={"to": "x@y.com", "password": "leaky", "api_key": "sk-abc"},
        outcome="success",
    ))
    body = store.notes[0]["content"]
    assert "leaky" not in body
    assert "sk-abc" not in body
    assert "[redacted]" in body


def test_save_skill_lesson_skips_unknown_topic(monkeypatch):
    store, _ = _install_fake_lb(monkeypatch)
    out = _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="tiktok",        # not in LEARNING_TOPICS
        action="tiktok_post",
        intent="Post a video",
        params={},
        outcome="success",
    ))
    assert out is None
    assert store.notes == []


def test_save_skill_lesson_skips_unknown_outcome(monkeypatch):
    store, _ = _install_fake_lb(monkeypatch)
    out = _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="n8n",
        action="n8n_create_workflow",
        intent="x",
        outcome="weird",
    ))
    assert out is None
    assert store.notes == []


def test_save_skill_lesson_writes_fix_outcome(monkeypatch):
    store, _ = _install_fake_lb(monkeypatch)
    _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="n8n", action="n8n_create_workflow:spreadsheet.create",
        intent="create sheet",
        params={"resource": "spreadsheet", "operation": "create", "title": "X"},
        outcome="fix",
        error='Node "Google Sheets": Missing or invalid required parameters',
        fix_summary="Added missing `title` parameter.",
    ))
    body = store.notes[0]["content"]
    assert "**Outcome:** fix" in body
    assert "Added missing `title`" in body
    tags = set(store.notes[0]["tags"])
    assert "outcome/fix" in tags


# ── Recall side ─────────────────────────────────────────────────────


def _install_fake_embeddings(monkeypatch, results):
    async def fake_semantic_search(config, user_id, query, *, k=10):
        return {"query": query, "results": results, "source": "semantic"}

    import lazyclaw.lazybrain.embeddings  # noqa: F401
    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search",
        fake_semantic_search,
    )


def test_recall_skill_lessons_filters_by_topic_and_outcome(monkeypatch):
    # Mix of right/wrong topic and outcome.
    results = [
        {
            "id": "a",
            "title": "Lesson (n8n/success): create sheet",
            "tags": ["lesson", "topic/n8n", "outcome/success"],
            "content": "**Topic:** n8n\n**Action:** n8n_create_workflow\n"
                       "**Intent:** create sheet\n**Outcome:** success\n\n"
                       "```json\n"
                       '{"resource": "spreadsheet", "operation": "create", "title": "X"}'
                       "\n```",
            "_score": 0.91,
        },
        {
            "id": "b",
            "title": "Lesson (email/fail): send",
            "tags": ["lesson", "topic/email", "outcome/fail"],
            "content": "**Outcome:** fail",
        },
        {
            "id": "c",
            "title": "Lesson (n8n/fail): broken",
            "tags": ["lesson", "topic/n8n", "outcome/fail"],
            "content": "**Outcome:** fail",
        },
        {
            "id": "d",
            "title": "Lesson (n8n/fix): repair",
            "tags": ["lesson", "topic/n8n", "outcome/fix"],
            "content": "**Topic:** n8n\n**Action:** n8n_update_workflow\n"
                       "**Intent:** repair\n**Outcome:** fix\n**Fix:** added title",
        },
    ]
    _install_fake_embeddings(monkeypatch, results)

    out = _run(mod.recall_skill_lessons(
        config=None, user_id="u1", topic="n8n",
        intent="create sheet", k=3,
    ))
    # Only n8n success + fix returned (fail filtered out, email dropped).
    ids = [r["note_id"] for r in out]
    assert "a" in ids
    assert "d" in ids
    assert "b" not in ids
    assert "c" not in ids
    # Body parsing extracted params from the JSON block.
    success = next(r for r in out if r["note_id"] == "a")
    assert success["params"]["title"] == "X"
    assert success["outcome"] == "success"


def test_recall_skill_lessons_empty_on_unknown_topic(monkeypatch):
    _install_fake_embeddings(monkeypatch, [])
    out = _run(mod.recall_skill_lessons(
        config=None, user_id="u1", topic="quantum", intent="x",
    ))
    assert out == []


def test_recall_skill_lessons_never_raises(monkeypatch):
    # Simulate embeddings module throwing.
    async def boom(*a, **kw):
        raise RuntimeError("ollama down and nothing to fall back on")

    import lazyclaw.lazybrain.embeddings  # noqa: F401
    monkeypatch.setattr("lazyclaw.lazybrain.embeddings.semantic_search", boom)

    out = _run(mod.recall_skill_lessons(
        config=None, user_id="u1", topic="n8n", intent="x",
    ))
    assert out == []


# ── Formatter ───────────────────────────────────────────────────────


def test_format_lessons_as_exemplars_produces_prompt_block():
    lessons = [
        {
            "intent": "Create Google Sheet named hirossa",
            "action": "n8n_create_workflow:spreadsheet.create",
            "outcome": "success",
            "params": {"resource": "spreadsheet", "operation": "create", "title": "hirossa"},
        },
    ]
    block = mod.format_lessons_as_exemplars(lessons)
    assert "Known-good past shapes" in block
    assert "Example 1" in block
    assert "hirossa" in block
    assert "```json" in block
    # Compact: should fit easily under 2 KB.
    assert len(block) < 2000


def test_format_lessons_empty_returns_empty_string():
    assert mod.format_lessons_as_exemplars([]) == ""
