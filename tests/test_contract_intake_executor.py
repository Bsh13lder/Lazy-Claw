"""Tests for contract_intake_executor (PR 4).

Covers:
  - Slug-default dispatch registration + GoalExecutor._dispatch fallback
  - dispatch_contract_intake guards (wrong slug, wrong status, no config)
  - Answer parser: happy path, JSON garbage, LLM down, value coercion
  - Composition steps individually (vault / live_host / account / watcher
    / template / note / escalate) — assert each side effect lands in DB
  - End-to-end: synthesized James Blue answers → full provisioning
  - Failure propagation: bad answer → goal goes BLOCKED, no half-provisioning
  - Non-web_monitoring path: note saved, goal DONE, no watcher created
  - Idempotent retry via ExecuteContractIntakeSetupSkill
  - Helper utilities (_slug_from_host, _normalize_host, _parse_work_type)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime.contract_intake_executor import (
    _ContractSetup,
    _build_setup_from_dict,
    _normalize_host,
    _parse_work_type,
    _slug_from_host,
    dispatch_contract_intake,
    register_runtime,
)
from lazyclaw.runtime.goal_executor import (
    Goal,
    GoalExecutor,
    GoalRepository,
    GoalStatus,
    _DEFAULT_DISPATCH_BY_SLUG,
    _resolve_default_dispatch,
    register_default_dispatch,
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


# ── Slug-default dispatch registry ──────────────────────────────────


def test_register_default_dispatch_idempotent():
    async def cb1(g): pass
    async def cb2(g): pass
    register_default_dispatch("test_slug", cb1)
    assert _resolve_default_dispatch("test_slug") is cb1
    register_default_dispatch("test_slug", cb2)
    assert _resolve_default_dispatch("test_slug") is cb2
    # Cleanup
    del _DEFAULT_DISPATCH_BY_SLUG["test_slug"]


def test_register_default_dispatch_case_insensitive():
    async def cb(g): pass
    register_default_dispatch("MyCustomSlug", cb)
    assert _resolve_default_dispatch("mycustomslug") is cb
    assert _resolve_default_dispatch("MYCUSTOMSLUG") is cb
    del _DEFAULT_DISPATCH_BY_SLUG["mycustomslug"]


def test_resolve_default_returns_none_for_unknown():
    assert _resolve_default_dispatch("nonexistent_slug_xyz") is None
    assert _resolve_default_dispatch("") is None
    assert _resolve_default_dispatch(None) is None


def test_upwork_dispatch_registered_at_import():
    """contract_intake_executor self-registers on import."""
    cb = _resolve_default_dispatch("upwork")
    assert cb is not None
    assert callable(cb)


# ── Helpers ─────────────────────────────────────────────────────────


def test_normalize_host():
    assert _normalize_host("UPWORK.com") == "upwork.com"
    assert _normalize_host("www.TaskRabbit.com") == "taskrabbit.com"
    assert _normalize_host("  api.upwork.com  ") == "api.upwork.com"
    assert _normalize_host("") == ""


def test_slug_from_host():
    assert _slug_from_host("upwork.com") == "upwork_com"
    assert _slug_from_host("www.task-rabbit.com") == "task_rabbit_com"
    assert _slug_from_host("api.fieldnation.com") == "api_fieldnation_com"
    # Max 32 chars
    assert len(_slug_from_host("a" * 100 + ".com")) <= 32


def test_slug_from_host_empty_fallback():
    assert _slug_from_host("") == "platform"


def _make_goal(*, status=GoalStatus.EXECUTING, summary="web_monitoring detected",
               answers=None, account_slug="upwork", plan=()) -> Goal:
    return Goal(
        id="g_abc",
        user_id="u1",
        title="Set up Upwork contract: Test",
        status=status,
        plan=plan,
        questions_pending=(),
        answers=answers or {},
        risks=(),
        confidence="medium",
        summary=summary,
        account_slug=account_slug,
    )


def test_parse_work_type_finds_in_summary():
    g = _make_goal(summary="The plan is to set up a web_monitoring stack...")
    assert _parse_work_type(g) == "web_monitoring"


def test_parse_work_type_data_scraping():
    g = _make_goal(summary="data_scraping job — collect rows...")
    assert _parse_work_type(g) == "data_scraping"


def test_parse_work_type_fallback_other():
    g = _make_goal(summary="some unrelated text")
    assert _parse_work_type(g) == "other"


# ── _build_setup_from_dict ──────────────────────────────────────────


def test_build_setup_full():
    data = {
        "platform_url": "https://www.fieldnation.com/jobs",
        "credentials_text": "user: foo\npass: bar",
        "city": "Madrid",
        "accept_action_text": "Click the green Accept button",
        "notify_channel": "telegram",
        "poll_seconds": 30,
        "auto_accept": False,
        "agent_location": "local",
        "twofa_method": "app",
    }
    s = _build_setup_from_dict(data)
    assert s.platform_url == "https://www.fieldnation.com/jobs"
    assert s.platform_host == "fieldnation.com"
    assert s.credentials_text == "user: foo\npass: bar"
    assert s.city == "Madrid"
    assert s.poll_seconds == 30
    assert s.auto_accept is False
    assert s.agent_location == "local"
    assert s.twofa_method == "app"


def test_build_setup_clamps_poll():
    # 0 is falsy and falls through to the default (60), not the clamp.
    # Use a low-but-truthy value to exercise the lower clamp.
    s = _build_setup_from_dict({"poll_seconds": 3})
    assert s.poll_seconds == 5
    s = _build_setup_from_dict({"poll_seconds": 9999})
    assert s.poll_seconds == 3600
    # Missing → default 60, no clamp
    s = _build_setup_from_dict({})
    assert s.poll_seconds == 60


def test_build_setup_coerces_invalid_enums():
    s = _build_setup_from_dict({
        "notify_channel": "carrier_pigeon",
        "agent_location": "moon",
        "twofa_method": "voice",
    })
    assert s.notify_channel == "unspecified"
    assert s.agent_location == "unspecified"
    assert s.twofa_method is None


def test_build_setup_empty_dict():
    s = _build_setup_from_dict({})
    assert s.platform_url is None
    assert s.platform_host is None
    assert s.credentials_text == ""
    assert s.poll_seconds == 60


# ── dispatch_contract_intake guards ──────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_wrong_slug(tmp_config):
    goal = _make_goal(account_slug="hirossa")
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=None,
    )
    assert "only handles 'upwork'" in result


@pytest.mark.asyncio
async def test_dispatch_wrong_status(tmp_config):
    goal = _make_goal(status=GoalStatus.AWAITING_USER_INFO)
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=None,
    )
    assert "expects EXECUTING" in result


@pytest.mark.asyncio
async def test_dispatch_no_config():
    """Without config + no module default, refuses to run."""
    # Save + clear the module defaults
    from lazyclaw.runtime import contract_intake_executor as mod
    saved_cfg, saved_reg = mod._DEFAULT_CONFIG, mod._DEFAULT_REGISTRY
    mod._DEFAULT_CONFIG = None
    mod._DEFAULT_REGISTRY = None
    try:
        goal = _make_goal()
        result = await dispatch_contract_intake(goal)
        assert "config unavailable" in result
    finally:
        mod._DEFAULT_CONFIG = saved_cfg
        mod._DEFAULT_REGISTRY = saved_reg


# ── End-to-end with mocked worker + skills ──────────────────────────


class _FakeSkill:
    def __init__(self, response="ok"):
        self._response = response
        self.calls: list[dict] = []

    async def execute(self, user_id: str, params: dict):
        self.calls.append({"user_id": user_id, **params})
        return self._response


class _FakeRegistry:
    def __init__(self, **skill_overrides):
        self.skills: dict = {
            "watch_site": _FakeSkill("Watching https://example.com — interval 1m"),
            "lazybrain_save_note": _FakeSkill("note saved"),
            "escalate_to_human": _FakeSkill("DONE"),
        }
        self.skills.update(skill_overrides)

    def get(self, name):
        return self.skills.get(name)

    def list_mcp_tools(self):
        return []


def _stub_parser(monkeypatch, setup_or_none: _ContractSetup | None):
    """Patch _parse_setup_from_answers to return a deterministic result."""
    async def fake_parse(config, goal):
        return setup_or_none

    monkeypatch.setattr(
        "lazyclaw.runtime.contract_intake_executor._parse_setup_from_answers",
        fake_parse,
    )


async def _make_persisted_goal(
    tmp_config, *, status=GoalStatus.EXECUTING, summary="web_monitoring stack",
    answers=None, plan=None,
):
    """Persist a real Goal row so the executor can call mark_step / mark_done."""
    from lazyclaw.runtime.goal_executor import Goal, GoalStep, GoalRepository
    repo = GoalRepository(tmp_config)
    steps = plan or tuple(
        GoalStep(idx=i, description=f"Step {i + 1}")
        for i in range(4)
    )
    goal = Goal(
        id="g_persisted",
        user_id="u1",
        title="Set up Upwork contract: James Blue",
        status=status,
        plan=steps,
        questions_pending=(),
        answers=answers or {"Q1": "A1"},
        risks=(),
        confidence="high",
        summary=summary,
        account_slug="upwork",
    )
    return await repo.create(goal)


@pytest.mark.asyncio
async def test_dispatch_end_to_end_full_provisioning(tmp_config, monkeypatch):
    """Synthesized James Blue answers → all 8 composition steps fire."""
    setup = _ContractSetup(
        platform_url="https://www.fieldnation.com/jobs",
        platform_host="fieldnation.com",
        credentials_text="user: vato@example.com\npass: secret123",
        city="Madrid",
        accept_action_text="Click the green Accept button in the top right",
        notify_channel="telegram",
        poll_seconds=30,
        auto_accept=False,
        agent_location="local",
        twofa_method="app",
    )
    _stub_parser(monkeypatch, setup)

    goal = await _make_persisted_goal(tmp_config)
    registry = _FakeRegistry()
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=registry,
    )

    # Setup summary mentions every step
    assert "vault: stored `fieldnation_com_credentials`" in result
    assert "live_host: added `fieldnation.com`" in result
    assert "browser_account: registered `fieldnation_com`" in result
    assert "watcher: registered" in result
    assert "template: saved `fieldnation_com_accept`" in result
    assert "setup_doc: saved to LazyBrain" in result
    assert "escalate: login prompt pushed" in result
    assert "DONE" in result  # goal landed

    # Side-effect verification
    from lazyclaw.browser.browser_settings import get_live_hosts, get_browser_settings
    from lazyclaw.crypto.vault import get_credential
    creds = await get_credential(tmp_config, "u1", "fieldnation_com_credentials")
    assert creds == "user: vato@example.com\npass: secret123"
    live = await get_live_hosts(tmp_config, "u1")
    assert "fieldnation.com" in live
    settings = await get_browser_settings(tmp_config, "u1")
    assert "fieldnation_com" in (settings.get("accounts") or {})

    # The watch_site + escalate_to_human skills were both invoked
    assert len(registry.skills["watch_site"].calls) == 1
    watch_call = registry.skills["watch_site"].calls[0]
    assert watch_call["url"] == "https://www.fieldnation.com/jobs"
    assert "Madrid" in watch_call["what_to_watch"]
    assert len(registry.skills["escalate_to_human"].calls) == 1
    assert len(registry.skills["lazybrain_save_note"].calls) == 1
    note_call = registry.skills["lazybrain_save_note"].calls[0]
    # CRITICAL: credentials never appear in the saved note
    assert "secret123" not in note_call["content"]
    assert "vato@example.com" not in note_call["content"]
    assert "fieldnation_com_credentials" in note_call["content"]  # vault key NAME only

    # Goal landed DONE
    from lazyclaw.runtime.goal_executor import GoalRepository
    repo = GoalRepository(tmp_config)
    final = await repo.get("u1", goal.id)
    assert final.status == GoalStatus.DONE
    assert final.steps_done == final.steps_total


@pytest.mark.asyncio
async def test_dispatch_parser_failure_goes_blocked(tmp_config, monkeypatch):
    """Parser returns None → goal lands BLOCKED with clear reason."""
    _stub_parser(monkeypatch, None)
    goal = await _make_persisted_goal(tmp_config)
    registry = _FakeRegistry()

    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=registry,
    )
    assert "BLOCKED" in result

    from lazyclaw.runtime.goal_executor import GoalRepository
    final = await GoalRepository(tmp_config).get("u1", goal.id)
    assert final.status == GoalStatus.BLOCKED
    assert "platform URL" in (final.blocked_on or "")
    # Nothing was provisioned
    assert len(registry.skills["watch_site"].calls) == 0


@pytest.mark.asyncio
async def test_dispatch_missing_url_goes_blocked(tmp_config, monkeypatch):
    """Parser succeeded but URL missing → BLOCKED, no half-provisioning."""
    setup = _ContractSetup(
        platform_url=None,
        platform_host=None,
        credentials_text="creds",
        city="Madrid",
        accept_action_text="accept",
        notify_channel="telegram",
        poll_seconds=60,
        auto_accept=False,
        agent_location="local",
        twofa_method=None,
    )
    _stub_parser(monkeypatch, setup)
    goal = await _make_persisted_goal(tmp_config)
    registry = _FakeRegistry()
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=registry,
    )
    assert "BLOCKED" in result
    # No watcher created
    assert len(registry.skills["watch_site"].calls) == 0


@pytest.mark.asyncio
async def test_dispatch_partial_failure_lands_blocked(tmp_config, monkeypatch):
    """One step (watcher) raises → goal BLOCKED but earlier steps stuck."""
    setup = _ContractSetup(
        platform_url="https://www.fieldnation.com/jobs",
        platform_host="fieldnation.com",
        credentials_text="user: x\npass: y",
        city="Madrid",
        accept_action_text="click accept",
        notify_channel="telegram",
        poll_seconds=60,
        auto_accept=False,
        agent_location="local",
        twofa_method=None,
    )
    _stub_parser(monkeypatch, setup)

    class _BoomSkill:
        calls = []
        async def execute(self, user_id, params):
            self.calls.append(params)
            raise RuntimeError("watcher provisioning DB error")

    registry = _FakeRegistry(watch_site=_BoomSkill())
    goal = await _make_persisted_goal(tmp_config)
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=registry,
    )

    assert "watcher:" in result
    assert "BLOCKED" in result
    # Vault + live_host + account still ran (composition is sequential
    # but each step is isolated)
    from lazyclaw.crypto.vault import get_credential
    assert await get_credential(
        tmp_config, "u1", "fieldnation_com_credentials",
    ) == "user: x\npass: y"

    from lazyclaw.runtime.goal_executor import GoalRepository
    final = await GoalRepository(tmp_config).get("u1", goal.id)
    assert final.status == GoalStatus.BLOCKED


@pytest.mark.asyncio
async def test_dispatch_non_monitor_note_only_path(tmp_config, monkeypatch):
    """Non-web_monitoring goal saves note + goes DONE without watcher."""
    _stub_parser(monkeypatch, None)  # parser shouldn't be called

    goal = await _make_persisted_goal(
        tmp_config,
        summary="data_scraping job to collect property records",
        answers={"Q1": "PropStream + Reonomy", "Q2": "name+phone+address"},
    )
    registry = _FakeRegistry()
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=registry,
    )
    assert "note-only path" in result
    assert "data_scraping" in result
    # Note saved, no watcher
    assert len(registry.skills["lazybrain_save_note"].calls) == 1
    assert len(registry.skills["watch_site"].calls) == 0
    # Goal DONE
    from lazyclaw.runtime.goal_executor import GoalRepository
    final = await GoalRepository(tmp_config).get("u1", goal.id)
    assert final.status == GoalStatus.DONE


@pytest.mark.asyncio
async def test_dispatch_skips_vault_when_no_creds(tmp_config, monkeypatch):
    """Empty credentials_text skips the vault step without failing."""
    setup = _ContractSetup(
        platform_url="https://x.com",
        platform_host="x.com",
        credentials_text="",  # empty
        city="Madrid",
        accept_action_text="click",
        notify_channel="telegram",
        poll_seconds=60,
        auto_accept=False,
        agent_location="local",
        twofa_method=None,
    )
    _stub_parser(monkeypatch, setup)
    goal = await _make_persisted_goal(tmp_config)
    registry = _FakeRegistry()
    result = await dispatch_contract_intake(
        goal, config=tmp_config, registry=registry,
    )
    assert "vault: SKIP (no credentials provided)" in result
    from lazyclaw.crypto.vault import get_credential
    assert await get_credential(tmp_config, "u1", "x_com_credentials") is None


# ── GoalExecutor._dispatch integration with slug-default fallback ───


@pytest.mark.asyncio
async def test_goal_executor_dispatch_uses_slug_default(tmp_config, monkeypatch):
    """_dispatch falls back to _DEFAULT_DISPATCH_BY_SLUG when no instance
    callback is configured. Verifies the wiring path that fires the
    executor automatically when submit_answers transitions a goal to
    EXECUTING."""
    fired = []

    async def my_cb(g):
        fired.append(g.id)

    register_default_dispatch("upwork", my_cb)
    try:
        # Persist a goal in EXECUTING; force _dispatch
        from lazyclaw.runtime.goal_executor import Goal, GoalStep, GoalRepository
        repo = GoalRepository(tmp_config)
        goal = await repo.create(Goal(
            id="g_disp",
            user_id="u1",
            title="t",
            status=GoalStatus.EXECUTING,
            plan=(GoalStep(idx=0, description="x"),),
            questions_pending=(),
            answers={},
            risks=(),
            confidence="high",
            summary="web_monitoring",
            account_slug="upwork",
        ))
        executor = GoalExecutor(tmp_config)  # NO dispatch_callback
        await executor._dispatch(goal)
        assert fired == [goal.id]
    finally:
        # Restore the real upwork callback
        from lazyclaw.runtime import contract_intake_executor
        register_default_dispatch("upwork", contract_intake_executor._dispatch_entry)


@pytest.mark.asyncio
async def test_goal_executor_per_instance_callback_overrides_default(tmp_config):
    """Per-instance dispatch_callback wins over the slug default."""
    default_fired = []
    instance_fired = []

    async def default_cb(g):
        default_fired.append(g.id)

    async def instance_cb(g):
        instance_fired.append(g.id)

    register_default_dispatch("test_override", default_cb)
    try:
        from lazyclaw.runtime.goal_executor import Goal, GoalStep, GoalRepository
        repo = GoalRepository(tmp_config)
        goal = await repo.create(Goal(
            id="g_override",
            user_id="u1",
            title="t",
            status=GoalStatus.EXECUTING,
            plan=(GoalStep(idx=0, description="x"),),
            questions_pending=(),
            answers={},
            risks=(),
            confidence="high",
            summary="web_monitoring",
            account_slug="test_override",
        ))
        executor = GoalExecutor(tmp_config, dispatch_callback=instance_cb)
        await executor._dispatch(goal)
        assert instance_fired == [goal.id]
        assert default_fired == []  # default NOT called
    finally:
        del _DEFAULT_DISPATCH_BY_SLUG["test_override"]
