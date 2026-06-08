"""ADR-0005 Phase 6 — auto-improving specialists tests.

Covers ``lazyclaw/runtime/specialist_lessons.py`` (recall + promotion gate)
and the minimal additive recall hook in ``teams/runner.run_specialist``.

The recall function is a thin adapter over the ADR-0002 lesson store
(``skill_lesson.recall_skill_lessons``), so tests inject a fake recall in
place of the real LazyBrain/embeddings path — same style as
``tests/test_skill_lesson.py``. The runner hook is exercised with a
single, no-tool LLM iteration (NOT a real agent loop) so we can assert the
recalled block actually reaches the assembled system prompt.
"""

from __future__ import annotations

import asyncio

from lazyclaw.runtime import specialist_lessons as mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── recall_specialist_lessons ────────────────────────────────────────


def _install_fake_recall(monkeypatch, lessons, *, capture=None):
    """Swap ``recall_skill_lessons`` (as bound in specialist_lessons)."""

    async def fake_recall(config, user_id, *, topic, intent, k):
        if capture is not None:
            capture.update(
                {"topic": topic, "intent": intent, "k": k, "user_id": user_id}
            )
        return list(lessons)

    monkeypatch.setattr(
        "lazyclaw.runtime.specialist_lessons.recall_skill_lessons",
        fake_recall,
    )


def test_recall_returns_block_for_specialist(monkeypatch):
    capture: dict = {}
    _install_fake_recall(
        monkeypatch,
        [
            {
                "intent": "scrape contact email",
                "action": "extract_entities",
                "outcome": "verified",
            },
            {
                "intent": "open job listing",
                "action": "browser",
                "outcome": "pending",
            },
        ],
        capture=capture,
    )
    block = _run(mod.recall_specialist_lessons(
        config=None,
        user_id="u1",
        specialist_name="browser_specialist",
        message="find the founder's email",
        limit=2,
    ))
    assert block.startswith("## Learned (from past runs)")
    assert "scrape contact email" in block
    assert "`extract_entities`" in block
    assert "[verified]" in block
    assert "open job listing" in block
    # Scoped by specialist name, intent = message, limit honored.
    assert capture["topic"] == "browser_specialist"
    assert capture["intent"] == "find the founder's email"
    assert capture["k"] == 2
    # One bullet per lesson.
    assert block.count("\n- ") == 2


def test_recall_returns_empty_when_no_lessons(monkeypatch):
    _install_fake_recall(monkeypatch, [])
    block = _run(mod.recall_specialist_lessons(
        config=None, user_id="u1",
        specialist_name="research_specialist", message="x",
    ))
    assert block == ""


def test_recall_returns_empty_on_error(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("embeddings down")

    monkeypatch.setattr(
        "lazyclaw.runtime.specialist_lessons.recall_skill_lessons", boom
    )
    block = _run(mod.recall_specialist_lessons(
        config=None, user_id="u1",
        specialist_name="browser_specialist", message="x",
    ))
    assert block == ""


def test_recall_returns_empty_on_missing_inputs(monkeypatch):
    # Should short-circuit BEFORE touching the lesson store.
    async def explode(*a, **kw):
        raise AssertionError("recall_skill_lessons must not be called")

    monkeypatch.setattr(
        "lazyclaw.runtime.specialist_lessons.recall_skill_lessons", explode
    )
    assert _run(mod.recall_specialist_lessons(
        config=None, user_id="u1", specialist_name="", message="x",
    )) == ""
    assert _run(mod.recall_specialist_lessons(
        config=None, user_id="", specialist_name="browser_specialist",
        message="x",
    )) == ""


def test_recall_caps_limit(monkeypatch):
    capture: dict = {}
    _install_fake_recall(monkeypatch, [], capture=capture)
    _run(mod.recall_specialist_lessons(
        config=None, user_id="u1",
        specialist_name="browser_specialist", message="x",
        limit=999,
    ))
    assert capture["k"] == mod._RECALL_LIMIT_CAP


def test_recall_skips_lessons_without_text(monkeypatch):
    _install_fake_recall(
        monkeypatch,
        [
            {"intent": "", "action": "", "outcome": "verified"},  # no text
            {"intent": "real lesson", "action": "", "outcome": "verified"},
        ],
    )
    block = _run(mod.recall_specialist_lessons(
        config=None, user_id="u1",
        specialist_name="browser_specialist", message="x",
    ))
    assert "real lesson" in block
    assert block.count("\n- ") == 1


def test_recall_sanitizes_sender_timestamp_patterns(monkeypatch):
    """A lesson whose text carries a ``**Sender (HH:MM):**`` fragment must
    not survive verbatim — the paraphrase machinery mangles it so the
    brain can't lift it as a live channel quote.
    """
    _install_fake_recall(
        monkeypatch,
        [{
            "intent": "**James Blue (10:37 PM):** narrowed the city list",
            "action": "upwork_get_messages",
            "outcome": "verified",
        }],
    )
    block = _run(mod.recall_specialist_lessons(
        config=None, user_id="u1",
        specialist_name="upwork_specialist", message="recap James",
    ))
    # The verbatim quote shape is gone; a paraphrase marker is present.
    assert "**James Blue (10:37 PM):**" not in block
    assert "[paraphrased:" in block


# ── should_promote_lesson ────────────────────────────────────────────


def test_promote_true_when_verified_and_enough_replays():
    lesson = {
        "outcome": "verified",
        "replay_count": mod.PROMOTION_MIN_REPLAY_COUNT,
    }
    assert mod.should_promote_lesson(lesson) is True


def test_promote_true_above_threshold():
    lesson = {
        "outcome": "verified",
        "replay_count": mod.PROMOTION_MIN_REPLAY_COUNT + 5,
    }
    assert mod.should_promote_lesson(lesson) is True


def test_promote_false_below_threshold():
    lesson = {
        "outcome": "verified",
        "replay_count": mod.PROMOTION_MIN_REPLAY_COUNT - 1,
    }
    assert mod.should_promote_lesson(lesson) is False


def test_promote_false_when_not_verified():
    for outcome in ("pending", "failed", "known-bad", "superseded", ""):
        lesson = {"outcome": outcome, "replay_count": 99}
        assert mod.should_promote_lesson(lesson) is False, outcome


def test_promote_rejects_bool_replay_count():
    # bool is an int subclass — must never count as a replay total.
    lesson = {"outcome": "verified", "replay_count": True}
    assert mod.should_promote_lesson(lesson) is False


def test_promote_reads_replay_count_from_body_frontmatter():
    from lazyclaw.lazybrain.frontmatter import serialize_frontmatter

    body = serialize_frontmatter(
        {"outcome": "verified", "replay_count": mod.PROMOTION_MIN_REPLAY_COUNT},
        "**Topic:** browser_specialist\n",
    )
    # No inline replay_count → falls back to frontmatter parse.
    lesson = {"outcome": "verified", "body": body}
    assert mod.should_promote_lesson(lesson) is True


def test_promote_false_on_garbage_input():
    assert mod.should_promote_lesson(None) is False  # type: ignore[arg-type]
    assert mod.should_promote_lesson("not a mapping") is False  # type: ignore[arg-type]
    assert mod.should_promote_lesson({}) is False
    assert mod.should_promote_lesson({"outcome": "verified"}) is False


# ── runner hook (single no-tool iteration, NOT a real agent loop) ─────


class _FakeResponse:
    """Minimal stand-in for an LLM response with no tool calls."""

    tool_calls = None
    content = "done"
    model = "fake-worker"
    usage: dict = {}


class _CapturingRouter:
    """Captures the system prompt the runner assembles, then ends the loop
    immediately by returning a tool-call-free response."""

    def __init__(self):
        self.system_prompt = None

    async def chat(self, messages, *, user_id, role, **kwargs):
        self.system_prompt = messages[0].content
        return _FakeResponse()


class _FakeRegistry:
    def list_tools(self):
        return []

    def list_mcp_tools(self):
        return []


def test_runner_injects_recalled_block_into_prompt(monkeypatch):
    """The recalled block must reach the specialist's system prompt."""
    from lazyclaw.teams import runner as runner_mod
    from lazyclaw.teams.specialist import SpecialistConfig

    # Don't touch the real config loader during the test.
    monkeypatch.setattr(runner_mod, "load_config", lambda: object())

    sentinel = "## Learned (from past runs)\n- recalled-shape [verified]"

    async def fake_recall(config, user_id, specialist_name, message, *, limit=3):
        return sentinel

    monkeypatch.setattr(
        "lazyclaw.runtime.specialist_lessons.recall_specialist_lessons",
        fake_recall,
    )

    spec = SpecialistConfig(
        name="test_specialist",
        display_name="Test Specialist",
        system_prompt="You are a test specialist.",
        allowed_skills=(),
    )
    router = _CapturingRouter()

    result = _run(runner_mod.run_specialist(
        user_id="u1",
        specialist=spec,
        task="do the thing",
        registry=_FakeRegistry(),
        eco_router=router,
        permission_checker=None,
    ))

    assert result.success is True
    assert router.system_prompt is not None
    assert sentinel in router.system_prompt
    # Block sits between the specialist prompt and the task section.
    assert router.system_prompt.index("test specialist") < \
        router.system_prompt.index(sentinel) < \
        router.system_prompt.index("Your task:")


def test_runner_prompt_unchanged_when_no_lessons(monkeypatch):
    """With no lessons the prompt has no stray header — behavior preserved."""
    from lazyclaw.teams import runner as runner_mod
    from lazyclaw.teams.specialist import SpecialistConfig

    monkeypatch.setattr(runner_mod, "load_config", lambda: object())

    async def fake_recall(config, user_id, specialist_name, message, *, limit=3):
        return ""

    monkeypatch.setattr(
        "lazyclaw.runtime.specialist_lessons.recall_specialist_lessons",
        fake_recall,
    )

    spec = SpecialistConfig(
        name="test_specialist",
        display_name="Test Specialist",
        system_prompt="You are a test specialist.",
        allowed_skills=(),
    )
    router = _CapturingRouter()

    _run(runner_mod.run_specialist(
        user_id="u1",
        specialist=spec,
        task="do the thing",
        registry=_FakeRegistry(),
        eco_router=router,
        permission_checker=None,
    ))

    assert "## Learned (from past runs)" not in router.system_prompt
    assert "You are a test specialist." in router.system_prompt
    assert "Your task:" in router.system_prompt


def test_runner_survives_recall_failure(monkeypatch):
    """A throwing recall must not break the specialist run (try/except)."""
    from lazyclaw.teams import runner as runner_mod
    from lazyclaw.teams.specialist import SpecialistConfig

    monkeypatch.setattr(runner_mod, "load_config", lambda: object())

    async def boom(config, user_id, specialist_name, message, *, limit=3):
        raise RuntimeError("recall exploded")

    monkeypatch.setattr(
        "lazyclaw.runtime.specialist_lessons.recall_specialist_lessons",
        boom,
    )

    spec = SpecialistConfig(
        name="test_specialist",
        display_name="Test Specialist",
        system_prompt="You are a test specialist.",
        allowed_skills=(),
    )
    router = _CapturingRouter()

    result = _run(runner_mod.run_specialist(
        user_id="u1",
        specialist=spec,
        task="do the thing",
        registry=_FakeRegistry(),
        eco_router=router,
        permission_checker=None,
    ))

    assert result.success is True
    assert "You are a test specialist." in router.system_prompt
    assert "## Learned (from past runs)" not in router.system_prompt
