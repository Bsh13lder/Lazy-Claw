"""Migration script — bucket / collapse / supersede.

Tests the pure logic (`_bucket_user_notes`, `_pick_survivor`,
`_extract_pair`, `_decode_tags`) without spinning up SQLite. The IO-bound
parts (`_rewrite_survivor`, `_supersede_others`) are exercised through a
fake `lazybrain.store` to keep the test suite hermetic.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from scripts import migrate_lessons_v2 as mig


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Pure helpers ────────────────────────────────────────────────────


def test_decode_tags_handles_json_string_and_list():
    assert mig._decode_tags('["a", "b"]') == ["a", "b"]
    assert mig._decode_tags(["a", "b"]) == ["a", "b"]
    assert mig._decode_tags(None) == []
    assert mig._decode_tags("garbage") == []


def test_extract_pair_returns_value_after_prefix():
    tags = ["topic/n8n", "action/n8n_create", "intent/foo-bar-baz"]
    assert mig._extract_pair(tags, "topic/") == "n8n"
    assert mig._extract_pair(tags, "action/") == "n8n_create"
    assert mig._extract_pair(tags, "intent/") == "foo-bar-baz"
    assert mig._extract_pair(tags, "absent/") == ""


def test_bucket_key_skips_notes_without_topic():
    assert mig._bucket_key(["lesson", "auto"]) is None
    assert mig._bucket_key(["topic/n8n", "auto"]) == ("n8n", "unknown", "no-intent")
    assert mig._bucket_key([
        "topic/email", "action/email_send", "intent/notify-user-x"
    ]) == ("email", "email_send", "notify-user-x")


def test_pick_survivor_prefers_success_over_failure():
    bucket = [
        {"id": "a", "tags": ["outcome/fail"], "importance": 9, "created_at": "2026-04-25"},
        {"id": "b", "tags": ["outcome/success"], "importance": 5, "created_at": "2026-04-26"},
        {"id": "c", "tags": ["outcome/fail"], "importance": 7, "created_at": "2026-04-27"},
    ]
    assert mig._pick_survivor(bucket)["id"] == "b"


def test_pick_survivor_falls_back_to_recency_when_no_success():
    bucket = [
        {"id": "a", "tags": ["outcome/fail"], "importance": 5, "created_at": "2026-04-25"},
        {"id": "b", "tags": ["outcome/fail"], "importance": 5, "created_at": "2026-04-27"},
    ]
    assert mig._pick_survivor(bucket)["id"] == "b"


def test_bucket_user_notes_groups_by_user_topic_action_intent():
    notes = [
        {
            "id": "1", "user_id": "u1",
            "tags_raw": json.dumps([
                "lesson", "auto", "topic/n8n", "action/n8n_create",
                "intent/foo", "outcome/success"
            ]),
            "importance": 6, "created_at": "2026-04-25", "updated_at": "2026-04-25",
        },
        {
            "id": "2", "user_id": "u1",
            "tags_raw": json.dumps([
                "lesson", "auto", "topic/n8n", "action/n8n_create",
                "intent/foo", "outcome/success"
            ]),
            "importance": 6, "created_at": "2026-04-26", "updated_at": "2026-04-26",
        },
        {
            "id": "3", "user_id": "u1",
            "tags_raw": json.dumps([
                "lesson", "auto", "topic/email", "action/email_send",
                "intent/bar"
            ]),
            "importance": 6, "created_at": "2026-04-26", "updated_at": "2026-04-26",
        },
        {
            "id": "4", "user_id": "u2",  # different user — separate bucket
            "tags_raw": json.dumps([
                "lesson", "auto", "topic/n8n", "action/n8n_create",
                "intent/foo"
            ]),
            "importance": 6, "created_at": "2026-04-26", "updated_at": "2026-04-26",
        },
    ]
    buckets = _run(mig._bucket_user_notes(notes))
    assert len(buckets) == 3
    assert ("u1", "n8n", "n8n_create", "foo") in buckets
    assert len(buckets[("u1", "n8n", "n8n_create", "foo")]) == 2
    assert ("u1", "email", "email_send", "bar") in buckets
    assert ("u2", "n8n", "n8n_create", "foo") in buckets


# ── Survivor / supersede with fake store ────────────────────────────


class _FakeStore:
    def __init__(self):
        self.notes: dict[str, dict] = {}
        self.update_calls: list = []

    async def get_note(self, config, user_id, note_id):
        return dict(self.notes.get(note_id) or {}) if note_id in self.notes else None

    async def update_note(self, config, user_id, note_id, **kw):
        self.update_calls.append((note_id, kw))
        match = self.notes.get(note_id)
        if match:
            if "content" in kw and kw["content"] is not None:
                match["content"] = kw["content"]
            if "tags" in kw and kw["tags"] is not None:
                match["tags"] = kw["tags"]
            if "importance" in kw and kw["importance"] is not None:
                match["importance"] = kw["importance"]
        return match


def _patch_store(monkeypatch, store):
    monkeypatch.setattr("lazyclaw.lazybrain.store.get_note", store.get_note)
    monkeypatch.setattr("lazyclaw.lazybrain.store.update_note", store.update_note)


def test_rewrite_survivor_dry_does_not_write(monkeypatch):
    store = _FakeStore()
    store.notes["a"] = {
        "id": "a", "user_id": "u1",
        "content": "**Topic:** n8n\n",
        "tags": ["lesson", "auto", "topic/n8n", "action/x", "outcome/success"],
        "importance": 6,
    }
    _patch_store(monkeypatch, store)

    survivor = {
        "id": "a", "user_id": "u1", "importance": 6,
        "tags": ["lesson", "auto", "topic/n8n", "action/x",
                 "intent/create-x", "outcome/success"],
        "created_at": "2026-04-25",
    }
    bucket = [survivor, dict(survivor, id="b", created_at="2026-04-27")]

    _run(mig._rewrite_survivor(
        config=None, survivor=survivor, bucket=bucket,
        apply=False, limit_per_bucket=10,
    ))
    assert store.update_calls == []  # dry-run never wrote


def test_rewrite_survivor_apply_collapses_into_shape(monkeypatch):
    store = _FakeStore()
    store.notes["a"] = {
        "id": "a", "user_id": "u1",
        "content": "**Topic:** n8n\n",
        "tags": ["lesson", "auto", "topic/n8n", "action/x", "outcome/success"],
        "importance": 6,
    }
    _patch_store(monkeypatch, store)

    survivor = {
        "id": "a", "user_id": "u1", "importance": 6,
        "tags": ["lesson", "auto", "topic/n8n", "action/x",
                 "intent/create-x", "outcome/success"],
        "created_at": "2026-04-25T10:00:00",
    }
    bucket = [
        survivor,
        dict(survivor, id="b", created_at="2026-04-26T10:00:00"),
        dict(survivor, id="c", created_at="2026-04-27T10:00:00"),
    ]

    _run(mig._rewrite_survivor(
        config=None, survivor=survivor, bucket=bucket,
        apply=True, limit_per_bucket=10,
    ))
    assert len(store.update_calls) == 1
    note_id, kw = store.update_calls[0]
    assert note_id == "a"
    assert "kind/shape" in kw["tags"]
    assert "outcome/verified" in kw["tags"]
    fm = kw["frontmatter_updates"]
    assert fm["replay_count"] == 3
    assert fm["first_used_at"] == "2026-04-25T10:00:00"
    assert fm["last_used_at"] == "2026-04-27T10:00:00"
    assert fm["migrated_from_lesson_v1"] is True
    assert "## Run log" in kw["content"]


def test_supersede_others_strips_lesson_tag_and_marks_legacy(monkeypatch):
    store = _FakeStore()
    store.notes["a"] = {"id": "a", "user_id": "u1", "tags": [], "importance": 5}
    store.notes["b"] = {"id": "b", "user_id": "u1", "tags": [], "importance": 5}
    _patch_store(monkeypatch, store)

    bucket = [
        {"id": "a", "user_id": "u1", "tags": ["lesson", "auto", "topic/n8n", "outcome/success"]},
        {"id": "b", "user_id": "u1", "tags": ["lesson", "auto", "topic/n8n", "outcome/fail"]},
    ]
    n = _run(mig._supersede_others(
        config=None, survivor_id="a", bucket=bucket, apply=True,
    ))
    assert n == 1
    note_id, kw = store.update_calls[0]
    assert note_id == "b"
    assert "lesson" not in kw["tags"]
    assert "kind/legacy" in kw["tags"]
    assert "outcome/superseded" in kw["tags"]
    # No leftover outcome/fail since we strip outcome/* before re-tagging.
    assert "outcome/fail" not in kw["tags"]
    fm = kw["frontmatter_updates"]
    assert fm["outcome"] == "superseded"
    assert fm["kind"] == "legacy"
    assert "superseded_at" in fm
