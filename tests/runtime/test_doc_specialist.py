"""Orchestration tests for the in-editor AI specialist.

A stub ECO router returns a SEQUENCE of canned LLM replies so we exercise plan
parsing, empty/no-op plan detection, the corrective apply-reject retry,
instruction validation, and the apply hand-off without any real model. The
specialist always uses the user's configured WORKER role (no brain/fallback
escalation), which these tests assert. The docs strategy is driven end-to-end
against a temp store.
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
    """Returns queued replies; records the role it was called with.

    The specialist should only ever call with ``role="worker"`` — the test
    suite asserts ``router.roles`` to guard against a regression that
    reintroduces brain/fallback escalation.
    """

    def __init__(self, replies):
        self._replies = list(replies)
        self.roles: list[str] = []
        self.messages_seen: list[list] = []

    async def chat(self, messages, user_id, role="brain", **kwargs):
        self.roles.append(role)
        self.messages_seen.append(list(messages))
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


# ── Happy path + parsing ────────────────────────────────────────────────


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


async def test_worker_garbage_then_corrective_retry(cfg):
    """Parse-fail on the worker triggers a corrective retry — on the WORKER."""
    did = await _doc(cfg)
    router = StubRouter([
        "sorry, I can't do that",                       # attempt 1: unparseable
        '{"paragraphs":["Recovered line"]}',            # attempt 2 (corrective): good
    ])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "add a line")
    assert res.ok
    assert router.roles == ["worker", "worker"]  # never brain/fallback
    assert "Recovered line" in D.get_text(res.snapshot)


# ── Empty / no-op plan → corrective retry ───────────────────────────────


async def test_empty_plan_first_then_populated_succeeds(cfg):
    """A structurally-valid-but-EMPTY plan is treated as a non-plan; the
    corrective retry on the SAME worker succeeds with a populated plan.
    """
    did = await _doc(cfg)
    router = StubRouter([
        '{"mode":"append","blocks":[]}',                    # attempt 1: empty → no-op
        '{"mode":"append","paragraphs":["Real content"]}',  # attempt 2 (corrective): good
    ])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "write something")
    assert res.ok
    assert "Real content" in D.get_text(res.snapshot)
    assert router.roles == ["worker", "worker"]  # configured worker only


async def test_empty_plan_pdf_fill_form(cfg):
    """The PDF strategy treats an empty fill_form as a no-op (per-kind check)."""
    from lazyclaw.pdf import ai_edit as P

    assert P.is_empty_plan({"op": "fill_form", "values": {}}) is True
    assert P.is_empty_plan({"op": "fill_form", "values": {"Name": "Ada"}}) is False


async def test_corrective_message_quotes_rejection_reason(cfg):
    """When apply rejects a plan, the corrective follow-up names the reason."""
    did = await _doc(cfg)
    # First plan parses + is non-empty by our normaliser? No — empty blocks
    # are caught as no-op before apply. To force an apply-ValueError we make a
    # plan that passes is_empty_plan but is rejected by apply. We monkeypatch
    # is_empty_plan to accept it so the apply layer is the rejecter.
    import lazyclaw.docs.ai_edit as docs_ai

    orig = docs_ai.is_empty_plan
    docs_ai.is_empty_plan = lambda plan: False  # let the empty plan reach apply
    try:
        router = StubRouter([
            '{"blocks":[]}',                              # worker: reaches apply → ValueError
            '{"paragraphs":["Saved by retry"]}',         # escalation: good
        ])
        res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "edit")
    finally:
        docs_ai.is_empty_plan = orig

    assert res.ok
    assert "Saved by retry" in D.get_text(res.snapshot)
    # The corrective (2nd attempt) message must quote the ValueError reason.
    corrective_msgs = router.messages_seen[-1]
    joined = "\n".join(m.content for m in corrective_msgs if hasattr(m, "content"))
    assert "REJECTED" in joined
    assert "non-empty 'blocks'" in joined  # the actual ValueError text


async def test_two_empty_plans_returns_friendly_error(cfg):
    """Empty/no-op plans across both worker attempts → ok=False with a hint."""
    did = await _doc(cfg)
    router = StubRouter([
        '{"blocks":[]}',   # attempt 1: empty
        '{"blocks":[]}',   # attempt 2 (corrective): empty
    ])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "do something")
    assert not res.ok
    assert "rephrasing" in res.error.lower() or "concrete" in res.error.lower()
    assert router.roles == ["worker", "worker"]


# ── Existing failure modes preserved ────────────────────────────────────


async def test_all_attempts_unparseable_returns_friendly_error(cfg):
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


async def test_only_ever_uses_configured_worker(cfg):
    """The specialist must NEVER call brain/fallback — it is exactly the user's
    configured worker, dynamically (any provider). Guards against a regression
    that reintroduces hardcoded model escalation."""
    did = await _doc(cfg)
    router = StubRouter([
        '{"blocks":[]}',                              # attempt 1: empty → no-op
        '{"paragraphs":["Worker did it"]}',          # attempt 2 (corrective): good
    ])
    res = await DS.run_doc_specialist(cfg, router, "u1", "docs", did, "write")
    assert res.ok
    assert "Worker did it" in D.get_text(res.snapshot)
    assert set(router.roles) == {"worker"}  # only the worker, every attempt
