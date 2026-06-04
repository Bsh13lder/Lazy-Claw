"""Orchestration tests for the in-editor AI specialist.

A stub ECO router returns canned LLM replies so we exercise plan parsing, the
worker→brain retry, instruction validation, and the apply hand-off without any
real model. The docs strategy is driven end-to-end against a temp store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import snapshot as D
from lazyclaw.docs.store import create_doc, get_doc, save_doc
from lazyclaw.runtime import doc_specialist as DS

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, content, model="stub"):
        self.content = content
        self.model = model


class StubRouter:
    """Returns queued replies; records the roles it was called with."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.roles: list[str] = []

    async def chat(self, messages, user_id, role="brain", **kwargs):
        self.roles.append(role)
        if not self._replies:
            return _Resp("{}")
        nxt = self._replies.pop(0)
        return nxt if isinstance(nxt, _Resp) else _Resp(nxt)


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _doc(cfg, text="Intro"):
    row = await create_doc(cfg, "u1", "Doc")
    snap = D.set_text(D.blank_document("Doc", doc_id=row["id"]), text)
    await save_doc(cfg, "u1", "Doc", snap, doc_id=row["id"])
    return row["id"]


async def test_happy_path_append_link(cfg):
    did = await _doc(cfg)
    router = StubRouter([
        '{"mode":"append","paragraphs":[{"runs":[{"text":"my site","url":"https://x.io"}]}]}'
    ])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "add my site link")
    assert res.ok
    assert res.snapshot is not None
    runs = [r for para in D.get_paragraph_runs(res.snapshot) for r in para]
    assert {"text": "my site", "url": "https://x.io"} in runs
    assert router.roles == ["worker"]  # parsed on first try, no brain retry


async def test_json_with_fence_and_prefix_is_parsed(cfg):
    did = await _doc(cfg)
    router = StubRouter(['[HYBRID groq] ```json\n{"paragraphs":["Hello there"]}\n```'])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "add hello")
    assert res.ok
    assert "Hello there" in D.get_text(res.snapshot)


async def test_worker_garbage_then_brain_retry(cfg):
    did = await _doc(cfg)
    router = StubRouter([
        "sorry, I can't do that",                       # worker: unparseable
        '{"paragraphs":["Recovered line"]}',            # brain: good
    ])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "add a line")
    assert res.ok
    assert router.roles == ["worker", "brain"]
    assert "Recovered line" in D.get_text(res.snapshot)


async def test_both_models_fail_returns_friendly_error(cfg):
    did = await _doc(cfg)
    router = StubRouter(["nope", "still nope"])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "do something")
    assert not res.ok
    assert "rephrasing" in res.error.lower()


async def test_empty_instruction_rejected(cfg):
    did = await _doc(cfg)
    router = StubRouter([])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "   ")
    assert not res.ok and "tell the ai" in res.error.lower()


async def test_unknown_kind(cfg):
    res = await DS.run_doc_specialist(cfg, StubRouter([]), "u1", "slides", "x", "y")
    assert not res.ok and "unknown" in res.error.lower()


async def test_missing_doc(cfg):
    router = StubRouter(['{"paragraphs":["x"]}'])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", "no-such-id", "edit")
    assert not res.ok and "not found" in res.error.lower()


async def test_apply_error_surfaces(cfg):
    did = await _doc(cfg)
    # valid JSON, but empty paragraphs → apply raises ValueError → friendly error
    router = StubRouter(['{"paragraphs":[]}'])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "edit")
    assert not res.ok and "couldn't apply" in res.error.lower()
