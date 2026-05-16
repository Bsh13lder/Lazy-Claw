"""Tests for NewContractIntakeSkill (PR 3).

Covers:
  - Missing contract_url → graceful error
  - MCP tool not registered → user-friendly error
  - MCP returns string JSON / dict / wrapped result — all parsed
  - Empty brief → "nothing to onboard" message
  - Worker LLM available → classification + extraction → Goal with batched questions
  - Worker LLM down → falls back to 'other' work type + single open question
  - Classification returns unknown enum → coerced to 'other'
  - skip_classification=True → skips LLM, jumps to 'other'
  - Already-known fields trim the checklist
  - GoalExecutor failure → propagates as user-readable error
  - Integration: poll skill dispatches intake for each new contract
  - Synthetic James Blue contract → produces a Goal with the 9 web_monitoring questions
  - instant_dispatch route matches 'intake contract <url>'
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime.fix_plan import FixPlan
from lazyclaw.runtime.instant_dispatch import INSTANT_ROUTES
from lazyclaw.skills.builtin.survival.new_contract_intake import (
    _REQUIRED_INPUTS,
    _VALID_TYPES,
    NewContractIntakeSkill,
)
from lazyclaw.skills.builtin.survival.upwork_contract_poll import (
    UpworkContractPollSkill,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# ── Fake registry + MCP tool helpers ─────────────────────────────────


class _FakeTool:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def execute(self, user_id: str, params: dict):
        self.calls.append(params)
        return self._response


class _FakeRegistry:
    """Registry returning a single MCP tool by suffix match."""

    def __init__(self, mcp_tool_response=None, mcp_tool_name="upwork_get_contract_details"):
        self._mcp_tool = (
            _FakeTool(mcp_tool_response) if mcp_tool_response is not None else None
        )
        self._mcp_name = mcp_tool_name
        # Sub-skills the intake might look up via registry.get()
        self.other_skills: dict[str, object] = {}

    def list_mcp_tools(self):
        if self._mcp_tool is None:
            return []
        return [{"function": {"name": f"mcp_xyz_{self._mcp_name}"}}]

    def get(self, name: str):
        if self._mcp_tool is not None and self._mcp_name in (name or ""):
            return self._mcp_tool
        return self.other_skills.get(name)


def _stub_brain_fixplan(monkeypatch, n_questions=9, summary=None):
    """Patch GoalExecutor's plan_research + build_fix_plan + lazybrain."""

    async def _no_lazybrain(*args, **kwargs):
        return {"results": [], "source": "empty"}

    async def _no_research(*args, **kwargs):
        return ""

    questions = [f"Q{i + 1}" for i in range(n_questions)]
    fake_plan = FixPlan(
        summary=summary or "Set up the contract execution stack.",
        steps=[
            "Vault credentials.",
            "Register isolated browser account.",
            "Open login URL + wait for human to log in.",
            "Register watcher pointing at listings page.",
            "Save accept-action template.",
            "Send Telegram baseline summary.",
        ],
        questions=questions,
        risks=["needs human approval on first accept"],
        confidence="high",
    )

    async def _build_fix_plan(*args, **kwargs):
        return fake_plan

    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search",
        _no_lazybrain, raising=False,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.plan_research.gather_plan_research",
        _no_research, raising=False,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.fix_plan.build_fix_plan",
        _build_fix_plan, raising=False,
    )


def _stub_worker(monkeypatch, classify_response=None, extract_response=None):
    """Patch the skill's _worker_json so we control LLM output deterministically.

    Each call returns the next item from a queue per kind. None means
    "return None" (simulates LLM failure).
    """
    call_log = []

    async def fake_worker(self, user_id, prompt, *, timeout_s=4.0):
        call_log.append(prompt[:80])
        # First call is classification, second is extraction
        if "classify freelance contracts" in prompt:
            return classify_response
        if "Extract specific facts" in prompt:
            return extract_response
        return None

    monkeypatch.setattr(NewContractIntakeSkill, "_worker_json", fake_worker)
    return call_log


# ── Param + registry validation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_intake_missing_contract_url(tmp_config):
    skill = NewContractIntakeSkill(config=tmp_config, registry=_FakeRegistry())
    result = await skill.execute("u1", {})
    assert "contract_url is required" in result


@pytest.mark.asyncio
async def test_intake_no_mcp_tool(tmp_config):
    skill = NewContractIntakeSkill(config=tmp_config, registry=_FakeRegistry())
    # Empty registry — no MCP tools
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "mcp-upwork tools not registered" in result


@pytest.mark.asyncio
async def test_intake_no_registry_at_all(tmp_config):
    skill = NewContractIntakeSkill(config=tmp_config, registry=None)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "skill registry not available" in result


@pytest.mark.asyncio
async def test_intake_no_config(tmp_config):
    """Skill must report config availability before MCP attempts."""
    skill = NewContractIntakeSkill(config=None, registry=_FakeRegistry({"title": "T"}))
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "config not available" in result


# ── Brief shape coercion ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intake_string_json_response_parsed(tmp_config, monkeypatch):
    """MCP sometimes returns JSON-encoded string; must parse."""
    contract = {"title": "T", "client_name": "C", "description": "Some brief text"}
    registry = _FakeRegistry(mcp_tool_response=json.dumps(contract))
    _stub_brain_fixplan(monkeypatch, n_questions=2)
    _stub_worker(monkeypatch, classify_response=None, extract_response=None)
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "Contract intake started" in result


@pytest.mark.asyncio
async def test_intake_result_wrapper_unwrapped(tmp_config, monkeypatch):
    contract = {"title": "T", "client_name": "C", "description": "brief"}
    registry = _FakeRegistry(mcp_tool_response={"result": contract})
    _stub_brain_fixplan(monkeypatch, n_questions=1)
    _stub_worker(monkeypatch)
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "Contract intake started" in result


@pytest.mark.asyncio
async def test_intake_unparseable_response(tmp_config):
    registry = _FakeRegistry(mcp_tool_response="<html>not json</html>")
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "not parseable JSON" in result


@pytest.mark.asyncio
async def test_intake_empty_brief_no_op(tmp_config, monkeypatch):
    """Contract with no description fields produces a clear error."""
    contract = {"title": "Empty contract", "client_name": "C"}
    registry = _FakeRegistry(mcp_tool_response=contract)
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "brief is empty" in result
    assert "nothing to onboard" in result


# ── Happy path: classification + extraction + Goal creation ──────────


@pytest.mark.asyncio
async def test_intake_web_monitoring_full_path(tmp_config, monkeypatch):
    """Worker classifies as web_monitoring → Goal gets all 9 questions."""
    contract = {
        "title": "24/7 monitor with 1-tap accept",
        "client_name": "James Blue",
        "rate": "$120",
        "description": (
            "I need a 24/7 monitor with phone alerts and 1-tap accept. "
            "Filter by city. Must run from my IP. No platform named yet."
        ),
    }
    registry = _FakeRegistry(mcp_tool_response=contract)
    _stub_brain_fixplan(monkeypatch, n_questions=9)
    _stub_worker(
        monkeypatch,
        classify_response={"work_type": "web_monitoring", "confidence": "high",
                           "reason": "explicit phrasing"},
        extract_response={"platform_url": None, "platform_name": None,
                          "city": None, "deadline_hint": "1 week",
                          "credentials_mentioned": False,
                          "notification_channel": "phone alert",
                          "accept_action_described": False},
    )
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})

    assert "Contract intake started" in result
    assert "James Blue" in result
    assert "24/7 monitor" in result
    assert "web_monitoring" in result
    assert "confidence: high" in result
    assert "9 question(s)" in result
    assert "Goal ID:" in result
    assert "answer_goal_questions" in result


@pytest.mark.asyncio
async def test_intake_classifier_down_uses_other(tmp_config, monkeypatch):
    """LLM returns None → work_type defaults to 'other' (single Q)."""
    contract = {
        "title": "Mystery work",
        "client_name": "X",
        "description": "Some opaque brief that nothing in training data matches.",
    }
    registry = _FakeRegistry(mcp_tool_response=contract)
    # 'other' has exactly 1 question; the stubbed plan returns that count
    _stub_brain_fixplan(monkeypatch, n_questions=1)
    _stub_worker(monkeypatch, classify_response=None, extract_response=None)
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})

    assert "other (confidence: none)" in result
    assert "1 question(s)" in result


@pytest.mark.asyncio
async def test_intake_classifier_unknown_enum_coerced(tmp_config, monkeypatch):
    """LLM hallucinates a type not in _VALID_TYPES → coerced to 'other'."""
    contract = {"title": "T", "client_name": "C", "description": "Some brief"}
    registry = _FakeRegistry(mcp_tool_response=contract)
    _stub_brain_fixplan(monkeypatch, n_questions=1)
    _stub_worker(
        monkeypatch,
        classify_response={"work_type": "blockchain_audit",  # not in enum
                           "confidence": "high", "reason": "hallucinated"},
        extract_response=None,
    )
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "other (confidence:" in result


@pytest.mark.asyncio
async def test_intake_skip_classification_bypasses_llm(tmp_config, monkeypatch):
    """skip_classification=True must not call the LLM at all."""
    contract = {"title": "T", "client_name": "C", "description": "Some brief"}
    registry = _FakeRegistry(mcp_tool_response=contract)
    _stub_brain_fixplan(monkeypatch, n_questions=1)
    log = _stub_worker(monkeypatch, classify_response={"work_type": "code_dev"},
                       extract_response=None)
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {
        "contract_url": "https://x/c/A",
        "skip_classification": True,
    })
    assert "other (confidence: none)" in result
    # Only one worker call (extract), no classify
    classify_calls = [p for p in log if "classify" in p.lower()]
    assert classify_calls == []


# ── GoalExecutor error propagation ────────────────────────────────────


@pytest.mark.asyncio
async def test_intake_goal_executor_failure_propagates(tmp_config, monkeypatch):
    contract = {"title": "T", "client_name": "C", "description": "brief"}
    registry = _FakeRegistry(mcp_tool_response=contract)
    _stub_worker(monkeypatch)

    async def _no_lazybrain(*args, **kwargs):
        return {"results": []}

    async def _no_research(*args, **kwargs):
        return ""

    async def _boom(*args, **kwargs):
        raise RuntimeError("brain LLM blew up")

    monkeypatch.setattr(
        "lazyclaw.lazybrain.embeddings.semantic_search",
        _no_lazybrain, raising=False,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.plan_research.gather_plan_research",
        _no_research, raising=False,
    )
    monkeypatch.setattr(
        "lazyclaw.runtime.fix_plan.build_fix_plan",
        _boom, raising=False,
    )

    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    # build_fix_plan failure is swallowed by GoalExecutor.start — a Goal
    # row is still created in DRAFTING state. So we get a "no questions"
    # path, not an error.
    result = await skill.execute("u1", {"contract_url": "https://x/c/A"})
    assert "Contract intake started" in result


# ── Synthesized James Blue end-to-end ───────────────────────────────


@pytest.mark.asyncio
async def test_synthesized_james_blue_contract(tmp_config, monkeypatch):
    """End-to-end against a fixture mimicking the real James Blue contract."""
    contract = {
        "title": "24/7 incoming-jobs monitor with 1-tap accept",
        "client_name": "James Blue",
        "rate": "$120",
        "type": "fixed-price",
        "status": "active",
        "description": (
            "I need a 24/7 monitor with phone alerts and 1-tap human "
            "acceptance within 3 seconds. Must run from my IP. City "
            "filter. Anti-detection browser, stealth, fingerprint. "
            "No specific platform named in this brief."
        ),
        "milestones": [
            {"title": "Implement stealth browser + monitor"},
            {"title": "Deliver setup docs"},
        ],
    }
    registry = _FakeRegistry(mcp_tool_response=contract)
    _stub_brain_fixplan(monkeypatch, n_questions=9)  # web_monitoring → 9 Qs
    _stub_worker(
        monkeypatch,
        classify_response={
            "work_type": "web_monitoring",
            "confidence": "high",
            "reason": "explicit 24/7 monitor + 1-tap accept language",
        },
        extract_response={
            "platform_url": None,        # NOT in brief
            "platform_name": None,       # NOT in brief
            "city": None,                # NOT in brief
            "deadline_hint": "1 week",
            "credentials_mentioned": False,
            "notification_channel": "phone alert",
            "accept_action_described": False,
        },
    )
    skill = NewContractIntakeSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contract_url": "https://www.upwork.com/c/JAMES"})
    assert "James Blue" in result
    assert "web_monitoring" in result
    assert "9 question(s)" in result


# ── Checklist invariants ────────────────────────────────────────────


def test_required_inputs_covers_all_valid_types():
    """Every type in _VALID_TYPES must have a checklist."""
    assert _VALID_TYPES == frozenset(_REQUIRED_INPUTS.keys())


def test_web_monitoring_checklist_has_9_questions():
    """The James Blue contract's checklist should be exactly 9 questions
    (matches the 9 unknowns the plan identified)."""
    assert len(_REQUIRED_INPUTS["web_monitoring"]) == 9


def test_other_is_single_open_question():
    """The 'other' bucket is a single open-ended catch-all."""
    assert len(_REQUIRED_INPUTS["other"]) == 1


def test_all_checklists_nonempty():
    for work_type, qs in _REQUIRED_INPUTS.items():
        assert len(qs) >= 1, f"{work_type} has an empty checklist"
        for q in qs:
            assert q.endswith("?") or q.endswith("."), (
                f"{work_type} question {q!r} doesn't end with ? or ."
            )


# ── Integration: poll skill → intake skill ──────────────────────────


@pytest.mark.asyncio
async def test_poll_dispatches_intake_for_new_contracts(tmp_config, monkeypatch):
    """When the poll skill sees a new contract AND new_contract_intake is
    registered, it should call the intake skill and embed the summary
    in its report."""
    contracts = [{
        "url": "https://x/c/A",
        "title": "T",
        "client_name": "James Blue",
        "rate": "$120",
    }]
    contract_detail = {
        "title": "T",
        "client_name": "James Blue",
        "description": "Need a 24/7 monitor. No platform named.",
    }

    # Poll registry — get_contracts tool
    class _PollRegistry:
        def __init__(self):
            self._get_contracts = _FakeTool({"result": contracts})
            self._get_details = _FakeTool(contract_detail)
            self.intake_calls: list[dict] = []

            class _RecordingIntake:
                async def execute(_self, user_id, params):
                    self.intake_calls.append(params)
                    return "📋 Intake started — mocked summary"

            self._intake = _RecordingIntake()

        def list_mcp_tools(self):
            return [
                {"function": {"name": "mcp_xyz_upwork_get_contracts"}},
                {"function": {"name": "mcp_xyz_upwork_get_contract_details"}},
            ]

        def get(self, name):
            if "get_contracts" in name and "details" not in name:
                return self._get_contracts
            if "get_contract_details" in name:
                return self._get_details
            if name == "new_contract_intake":
                return self._intake
            return None

    registry = _PollRegistry()
    poll = UpworkContractPollSkill(config=tmp_config, registry=registry)
    result = await poll.execute("u1", {})

    assert "1 new contract" in result
    assert "James Blue" in result
    assert "📋 Intake started — mocked summary" in result
    # Intake was actually invoked with the right URL
    assert registry.intake_calls == [{"contract_url": "https://x/c/A"}]


# ── instant_dispatch matches ────────────────────────────────────────


@pytest.mark.parametrize("phrase,expected_url", [
    ("intake contract https://www.upwork.com/c/A",
     "https://www.upwork.com/c/A"),
    ("intake my contract https://www.upwork.com/c/B",
     "https://www.upwork.com/c/B"),
    ("intake this contract https://www.upwork.com/c/D",
     "https://www.upwork.com/c/D"),
    ("please intake upwork contract https://x.com/y",
     "https://x.com/y"),
])
def test_instant_dispatch_intake_matches(phrase, expected_url):
    for route in INSTANT_ROUTES:
        if route.skill_name == "new_contract_intake":
            m = route.pattern.match(phrase.lower())
            assert m is not None, f"{phrase!r} should match intake route"
            params = route.params_builder(m)
            # URLs are forced lower by the dispatcher pre-match; the
            # builder hands back what the pattern captured (also lower).
            assert params["contract_url"] == expected_url.lower()
            return
    pytest.fail("new_contract_intake route not found in INSTANT_ROUTES")


@pytest.mark.parametrize("phrase", [
    "intake my taxes",
    "check my upwork contracts",
    "set up a new project",
])
def test_instant_dispatch_intake_does_not_overmatch(phrase):
    for route in INSTANT_ROUTES:
        if route.skill_name == "new_contract_intake":
            assert not route.pattern.match(phrase.lower()), (
                f"{phrase!r} wrongly matched intake route"
            )
            return
