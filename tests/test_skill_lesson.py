"""Pillar B tests — cross-topic skill-outcome lesson store.

Updated 2026-04-29 for the Obsidian-borrowed redesign:

  * `_redact` drops sensitive keys at every nesting depth + truncates
    long strings (never leaks secrets into a PKM note).
  * `save_skill_lesson` upserts ONE note per `(topic, action, intent)`
    triple. First write creates a `kind/shape` card; subsequent calls
    bump `replay_count` in frontmatter instead of producing duplicates.
  * Outcome state machine: pending / verified / failed / superseded /
    known-bad. Legacy "success" → pending, "fail" → failed, "fix" →
    verified.
  * `recall_skill_lessons` filters on `kind/shape` AND `outcome/verified`
    by default — only confirmed-good shapes feed the few-shot pool.
  * `recall_known_bad_shapes` returns negative examples separately.
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
    """In-memory stand-in for ``lazyclaw.lazybrain.store`` honoring the
    new save_note(frontmatter=...) and update_note(frontmatter_updates=...)
    surface. Tests bind to it via ``_install_fake_lb``.
    """

    def __init__(self):
        self.notes: list[dict] = []

    def _serialize(self, frontmatter, content):
        # Mirror the production lib so our body assertions can probe
        # both the readable body and the YAML block.
        from lazyclaw.lazybrain.frontmatter import serialize_frontmatter
        if frontmatter:
            return serialize_frontmatter(frontmatter, content)
        return content

    async def save_note(
        self, config, user_id, *, content, title, tags, importance,
        frontmatter=None, **kw,
    ):
        note = {
            "id": f"n{len(self.notes) + 1}",
            "content": self._serialize(frontmatter, content),
            "title": title,
            "tags": tags,
            "importance": importance,
            "user_id": user_id,
        }
        self.notes.append(note)
        return note

    async def update_note(
        self, config, user_id, note_id, *, content=None, tags=None,
        importance=None, frontmatter_updates=None, **kw,
    ):
        # Find the note in our list.
        match = next((n for n in self.notes if n["id"] == note_id), None)
        if not match:
            return None
        if content is not None:
            match["content"] = content
        if tags is not None:
            match["tags"] = tags
        if importance is not None:
            match["importance"] = importance
        if frontmatter_updates is not None:
            from lazyclaw.lazybrain.frontmatter import merge_frontmatter
            match["content"] = merge_frontmatter(
                match["content"], frontmatter_updates
            )
        return dict(match)

    async def get_note(self, config, user_id, note_id):
        match = next((n for n in self.notes if n["id"] == note_id), None)
        return dict(match) if match else None


class _FakeLBEvents:
    def __init__(self):
        self.published: list = []

    def publish_note_saved(self, *a, **kw):
        self.published.append((a, kw))


def _install_fake_lb(monkeypatch, *, existing_id_for_key=None):
    """Replace `save_note` + `update_note` + `publish_note_saved` in-place.

    Also stubs out the upsert lookup ``_find_by_title_key`` since it goes
    through a raw ``db_session`` that's awkward to mock. By default the
    fake reports "no existing note" so every call goes through the create
    path; pass ``existing_id_for_key`` to flip a specific key into the
    upsert path."""
    store = _FakeLBStore()
    events = _FakeLBEvents()
    # Import so attributes exist on the parent package, then swap them.
    import lazyclaw.lazybrain.events  # noqa: F401
    import lazyclaw.lazybrain.store   # noqa: F401
    monkeypatch.setattr("lazyclaw.lazybrain.store.save_note", store.save_note)
    monkeypatch.setattr(
        "lazyclaw.lazybrain.store.update_note", store.update_note
    )
    monkeypatch.setattr(
        "lazyclaw.lazybrain.store.get_note", store.get_note
    )
    monkeypatch.setattr(
        "lazyclaw.lazybrain.events.publish_note_saved",
        events.publish_note_saved,
    )

    async def fake_find(config, user_id, title_key):
        if not existing_id_for_key:
            return None
        wanted = existing_id_for_key.get(title_key)
        if not wanted:
            return None
        return await store.get_note(config, user_id, wanted)

    monkeypatch.setattr(
        "lazyclaw.runtime.skill_lesson._find_by_title_key", fake_find
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
        outcome="success",  # legacy → normalises to OUTCOME_PENDING
    ))
    assert note_id == "n1"
    assert len(store.notes) == 1
    note = store.notes[0]
    tags = set(note["tags"])
    assert "lesson" in tags
    assert "auto" in tags
    assert "owner/agent" in tags
    assert "kind/shape" in tags
    assert "topic/n8n" in tags
    # Legacy "success" gets normalised to the new "pending" state.
    assert "outcome/pending" in tags
    assert "action/n8n_create_workflow" in tags
    assert any(t.startswith("intent/create-google-sheet") for t in tags)
    # Canonical title format is the upsert handle for the new design.
    assert note["title"].startswith("Skill shape · n8n/n8n_create_workflow · ")
    # Body contains the human-readable fields.
    assert "**Topic:** n8n" in note["content"]
    assert "hirossa keyword research" in note["content"]
    # Frontmatter exposes the machine-readable state.
    from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    props, _, has_fm = parse_frontmatter(note["content"])
    assert has_fm is True
    assert props["kind"] == "shape"
    assert props["outcome"] == "pending"
    assert props["replay_count"] == 1
    assert "first_used_at" in props
    assert "last_used_at" in props
    # Run log section present with one entry.
    assert "## Run log" in note["content"]
    assert "pending" in note["content"]
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


def test_save_skill_lesson_accepts_arbitrary_topic(monkeypatch):
    """LEARNING_TOPICS used to be a hardcoded whitelist that dropped any
    topic not explicitly listed. The dispatcher's universal post-skill
    hook needs the gate inverted: every topic is allowed unless it lands
    in the (empty by default) ``_TOPIC_DENYLIST``.
    """
    store, _ = _install_fake_lb(monkeypatch)
    out = _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="tiktok",        # arbitrary, never explicitly registered
        action="tiktok_post",
        intent="Post a video",
        params={},
        outcome="success",
    ))
    assert out == "n1"
    assert len(store.notes) == 1
    assert "topic/tiktok" in store.notes[0]["tags"]


def test_save_skill_lesson_skips_empty_topic(monkeypatch):
    store, _ = _install_fake_lb(monkeypatch)
    out = _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="",
        action="x", intent="x", params={}, outcome="success",
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
    """Legacy ``outcome="fix"`` maps to the new ``OUTCOME_VERIFIED`` state."""
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
    assert "Added missing `title`" in body
    tags = set(store.notes[0]["tags"])
    assert "outcome/verified" in tags
    assert "kind/shape" in tags
    # Verified → high importance (7) so it surfaces in recall.
    assert store.notes[0]["importance"] == 7


def test_save_skill_lesson_upserts_on_replay(monkeypatch):
    """Same (topic, action, intent_slug) → one note, replay_count++."""
    from lazyclaw.lazybrain.store import _title_key
    canonical = mod._canonical_title(
        "n8n", "n8n_create_workflow", "create-google-sheet"
    )
    key = _title_key(canonical)
    store, _ = _install_fake_lb(
        monkeypatch, existing_id_for_key={key: "n1"}
    )
    # Seed an existing card.
    _run(store.save_note(
        config=None, user_id="u1", content="**Topic:** n8n\n",
        title=canonical, tags=["kind/shape", "topic/n8n"], importance=4,
        frontmatter={
            "kind": "shape", "topic": "n8n", "action": "n8n_create_workflow",
            "intent": "Create Google Sheet", "outcome": "pending",
            "replay_count": 1, "first_used_at": "2026-04-28T10:00:00Z",
            "last_used_at": "2026-04-28T10:00:00Z",
        },
    ))
    # Now replay the same shape — should update, not duplicate.
    _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="n8n", action="n8n_create_workflow:spreadsheet.create",
        intent="Create Google Sheet named hirossa",
        params={"title": "hirossa"},
        outcome="success",
    ))
    # Still exactly one note in the store.
    assert len(store.notes) == 1
    body = store.notes[0]["content"]
    from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    props, _, _ = parse_frontmatter(body)
    assert props["replay_count"] == 2
    # Run log appended.
    assert body.count("- ") >= 1
    assert "## Run log" in body


def test_save_skill_lesson_demotes_verified_on_fresh_failure(monkeypatch):
    """A fresh ``failed`` write against a ``verified`` card flips it back
    to ``pending`` (re-test) instead of marking the card broken outright.
    """
    from lazyclaw.lazybrain.store import _title_key
    canonical = mod._canonical_title("n8n", "n8n_create_workflow", "create-sheet")
    key = _title_key(canonical)
    store, _ = _install_fake_lb(
        monkeypatch, existing_id_for_key={key: "n1"}
    )
    _run(store.save_note(
        config=None, user_id="u1", content="**Topic:** n8n\n",
        title=canonical,
        tags=["kind/shape", "topic/n8n", "outcome/verified"],
        importance=7,
        frontmatter={
            "kind": "shape", "topic": "n8n",
            "action": "n8n_create_workflow",
            "intent": "create sheet", "outcome": "verified",
            "replay_count": 5,
        },
    ))
    _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="n8n", action="n8n_create_workflow",
        intent="create sheet",
        outcome="fail",   # legacy → OUTCOME_FAILED
        error="boom",
    ))
    from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    props, _, _ = parse_frontmatter(store.notes[0]["content"])
    # Verified did NOT flip directly to failed — it dropped to pending.
    assert props["outcome"] == "pending"
    # Replay count still bumped.
    assert props["replay_count"] == 6
    tags = set(store.notes[0]["tags"])
    assert "outcome/pending" in tags
    assert "outcome/failed" not in tags


def test_save_skill_lesson_promotes_to_verified(monkeypatch):
    """Calling with explicit ``outcome=OUTCOME_VERIFIED`` upgrades the card."""
    from lazyclaw.lazybrain.store import _title_key
    canonical = mod._canonical_title("email", "email_send", "notify-user")
    key = _title_key(canonical)
    store, _ = _install_fake_lb(
        monkeypatch, existing_id_for_key={key: "n1"}
    )
    _run(store.save_note(
        config=None, user_id="u1", content="**Topic:** email\n",
        title=canonical, tags=["kind/shape", "topic/email"], importance=4,
        frontmatter={
            "kind": "shape", "topic": "email", "action": "email_send",
            "intent": "Notify user", "outcome": "pending",
            "replay_count": 3, "pending_since_turn": 7,
        },
    ))
    _run(mod.save_skill_lesson(
        config=None, user_id="u1",
        topic="email", action="email_send",
        intent="Notify user",
        outcome=mod.OUTCOME_VERIFIED,
    ))
    from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    props, _, _ = parse_frontmatter(store.notes[0]["content"])
    assert props["outcome"] == "verified"
    # Verified clears the pending_since_turn marker.
    assert "pending_since_turn" not in props


# ── Recall side ─────────────────────────────────────────────────────


def _install_fake_embeddings(monkeypatch, results):
    # Accept the same keyword args the production semantic_search now takes
    # (``tag_prefix`` for SQL-layer prefilter, ``min_similarity`` for the
    # weak-hit threshold). The fake ignores them — tests control what
    # comes back independently of which knobs the caller turns.
    async def fake_semantic_search(
        config, user_id, query, *, k=10, tag_prefix=None, min_similarity=0.0,
    ):
        return {"query": query, "results": results, "source": "semantic"}

    import lazyclaw.lazybrain.embeddings  # noqa: F401
    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search",
        fake_semantic_search,
    )


def test_recall_skill_lessons_filters_by_topic_and_outcome(monkeypatch):
    """Default positive set is ``kind/shape`` + (verified OR pending).

    Pending shapes count because the verifier pump takes 3 turns to
    promote them — gating recall on verified-only would create a dead
    zone right after a successful first run, which is when recall is
    most useful.
    """
    results = [
        {
            "id": "a",
            "title": "Skill shape · n8n/n8n_create_workflow · create-sheet",
            "tags": ["lesson", "kind/shape", "topic/n8n", "outcome/verified"],
            "content": "**Topic:** n8n\n**Action:** n8n_create_workflow\n"
                       "**Intent:** create sheet\n\n"
                       "```json\n"
                       '{"resource": "spreadsheet", "operation": "create", "title": "X"}'
                       "\n```",
            "_score": 0.91,
        },
        {
            "id": "b",  # wrong topic
            "title": "Skill shape · email/...",
            "tags": ["kind/shape", "topic/email", "outcome/verified"],
            "content": "",
        },
        {
            "id": "c",  # right topic but failed — must NOT surface
            "title": "Skill shape · n8n/broken",
            "tags": ["kind/shape", "topic/n8n", "outcome/failed"],
            "content": "",
        },
        {
            "id": "d",  # pending — should surface alongside verified
            "title": "Skill shape · n8n/draft",
            "tags": ["kind/shape", "topic/n8n", "outcome/pending"],
            "content": "",
        },
        {
            "id": "e",  # right topic but wrong kind
            "title": "Some user note",
            "tags": ["kind/note", "topic/n8n", "outcome/verified"],
            "content": "",
        },
        {
            "id": "f",  # known-bad — must NEVER appear in positive recall
            "title": "Skill shape · n8n/cursed",
            "tags": ["kind/shape", "topic/n8n", "outcome/known-bad"],
            "content": "",
        },
        {
            "id": "g",  # superseded — collapsed by migration, must not surface
            "title": "Skill shape · n8n/old",
            "tags": ["kind/shape", "topic/n8n", "outcome/superseded"],
            "content": "",
        },
    ]
    _install_fake_embeddings(monkeypatch, results)

    out = _run(mod.recall_skill_lessons(
        config=None, user_id="u1", topic="n8n",
        intent="create sheet", k=4,
    ))
    ids = sorted([r["note_id"] for r in out])
    assert ids == ["a", "d"]


def test_recall_skill_lessons_explicit_verified_only(monkeypatch):
    """Callers that need only verified pass ``outcomes=("verified",)``."""
    results = [
        {
            "id": "a", "title": "verified",
            "tags": ["kind/shape", "topic/n8n", "outcome/verified"],
            "content": "",
        },
        {
            "id": "b", "title": "pending",
            "tags": ["kind/shape", "topic/n8n", "outcome/pending"],
            "content": "",
        },
    ]
    _install_fake_embeddings(monkeypatch, results)
    out = _run(mod.recall_skill_lessons(
        config=None, user_id="u1", topic="n8n", intent="x",
        outcomes=("verified",),
    ))
    assert [r["note_id"] for r in out] == ["a"]


def test_recall_known_bad_returns_anti_examples(monkeypatch):
    results = [
        {
            "id": "x",
            "title": "Skill shape · n8n/cursed",
            "tags": ["kind/shape", "topic/n8n", "outcome/known-bad"],
            "content": "**Action:** n8n_bad\n**Intent:** broken way",
        },
        {
            "id": "y",
            "title": "Skill shape · n8n/good",
            "tags": ["kind/shape", "topic/n8n", "outcome/verified"],
            "content": "",
        },
    ]
    _install_fake_embeddings(monkeypatch, results)
    out = _run(mod.recall_known_bad_shapes(
        config=None, user_id="u1", topic="n8n", intent="x",
    ))
    assert [r["note_id"] for r in out] == ["x"]


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
            "outcome": "verified",
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


def test_format_known_bad_warnings_prompt_block():
    bad = [
        {
            "intent": "Wrong way to send",
            "action": "email_send_legacy",
            "params": {"to": "x@y.com", "smtp_v1": True},
        },
    ]
    block = mod.format_known_bad_as_warnings(bad)
    assert "Avoid these known-bad" in block
    assert "Anti-example 1" in block
    assert "smtp_v1" in block
    assert "Do not replay" in block
    assert mod.format_known_bad_as_warnings([]) == ""
