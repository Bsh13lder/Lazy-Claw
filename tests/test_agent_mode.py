"""Tests for operating modes (ADR-0005 Phase 3)."""
from __future__ import annotations

import pytest

from lazyclaw.permissions.models import ALLOW, ASK, DENY, ResolvedPermission
from lazyclaw.runtime.agent_mode import (
    AgentMode,
    DEFAULT_MODE,
    apply_mode_posture,
    get_agent_mode,
    invalidate_mode_cache,
    is_readonly_skill,
    parse_mode,
)


def _rp(level: str, name: str = "x") -> ResolvedPermission:
    return ResolvedPermission(skill_name=name, level=level, source="test")


# ── parse_mode ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, AgentMode.ASK),
        ("", AgentMode.ASK),
        ("chat", AgentMode.CHAT),
        ("ASK", AgentMode.ASK),
        (" Plan ", AgentMode.PLAN),
        ("auto", AgentMode.AUTO),
        ("garbage", AgentMode.ASK),
    ],
)
def test_parse_mode(raw, expected):
    assert parse_mode(raw) is expected


def test_default_mode_is_ask():
    assert DEFAULT_MODE is AgentMode.ASK


# ── is_readonly_skill ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,readonly",
    [
        ("web_search", True),
        ("search_tools", True),
        ("recall_memories", True),
        ("list_tasks", True),
        ("get_contract", True),
        ("read_file", True),
        ("upwork_get_messages", True),  # _get_ infix
        ("send_email", False),
        ("browser", False),
        ("add_task", False),
        ("fill_pdf_form", False),
    ],
)
def test_is_readonly_skill(name, readonly):
    assert is_readonly_skill(name) is readonly


def test_is_readonly_by_category():
    assert is_readonly_skill("anything_weird", category="research") is True


# ── apply_mode_posture ────────────────────────────────────────────────


@pytest.mark.parametrize("level", [ALLOW, ASK, DENY])
def test_ask_mode_is_noop(level):
    base = _rp(level)
    out = apply_mode_posture(AgentMode.ASK, base, is_readonly=False)
    assert out is base  # unchanged identity → zero behavior change


@pytest.mark.parametrize("level", [ALLOW, ASK, DENY])
@pytest.mark.parametrize("ro", [True, False])
def test_chat_mode_denies_everything(level, ro):
    out = apply_mode_posture(AgentMode.CHAT, _rp(level), is_readonly=ro)
    assert out.level == DENY


def test_auto_mode_promotes_ask_to_allow():
    assert apply_mode_posture(AgentMode.AUTO, _rp(ASK), is_readonly=False).level == ALLOW


def test_auto_mode_keeps_deny():
    assert apply_mode_posture(AgentMode.AUTO, _rp(DENY), is_readonly=False).level == DENY


def test_auto_mode_keeps_allow():
    base = _rp(ALLOW)
    assert apply_mode_posture(AgentMode.AUTO, base, is_readonly=False) is base


def test_plan_mode_allows_readonly():
    base = _rp(ALLOW)
    assert apply_mode_posture(AgentMode.PLAN, base, is_readonly=True) is base


def test_plan_mode_gates_writes_to_ask():
    assert apply_mode_posture(AgentMode.PLAN, _rp(ALLOW), is_readonly=False).level == ASK


def test_plan_mode_keeps_deny_on_writes():
    assert apply_mode_posture(AgentMode.PLAN, _rp(DENY), is_readonly=False).level == DENY


def test_posture_source_tag_on_change():
    out = apply_mode_posture(AgentMode.CHAT, _rp(ALLOW), is_readonly=True)
    assert out.source == "mode:chat"


# ── get_agent_mode (cache) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_agent_mode_reads_and_caches(monkeypatch):
    invalidate_mode_cache()
    calls = {"n": 0}

    async def fake_general(config, user_id):
        calls["n"] += 1
        return {"agent_mode": "auto"}

    monkeypatch.setattr(
        "lazyclaw.settings.general.get_general_settings", fake_general
    )
    m1 = await get_agent_mode(config=None, user_id="u1")
    m2 = await get_agent_mode(config=None, user_id="u1")
    assert m1 is AgentMode.AUTO and m2 is AgentMode.AUTO
    assert calls["n"] == 1  # second call served from cache
    invalidate_mode_cache("u1")


@pytest.mark.asyncio
async def test_get_agent_mode_defaults_on_error(monkeypatch):
    invalidate_mode_cache()

    async def boom(config, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr("lazyclaw.settings.general.get_general_settings", boom)
    assert await get_agent_mode(config=None, user_id="u2") is AgentMode.ASK
    invalidate_mode_cache()


# ── settings validation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_rejects_invalid_mode():
    from lazyclaw.settings.general import update_general_settings

    # Invalid value raises during validation, before any DB access.
    with pytest.raises(ValueError, match="agent_mode"):
        await update_general_settings(None, "u3", {"agent_mode": "bogus"})


def test_default_general_has_ask_mode():
    from lazyclaw.settings.general import DEFAULT_GENERAL, VALID_AGENT_MODES

    assert DEFAULT_GENERAL["agent_mode"] == "ask"
    assert VALID_AGENT_MODES == {m.value for m in AgentMode}


# ── checker.check_effective integration ───────────────────────────────


@pytest.mark.asyncio
async def test_check_effective_applies_posture(monkeypatch):
    from lazyclaw.permissions.checker import PermissionChecker

    class _FakeSkill:
        category = "communication"

    class _FakeRegistry:
        def get(self, name):
            return _FakeSkill()

    checker = PermissionChecker(config=None, registry=_FakeRegistry())

    async def fake_check(user_id, skill_name):
        return _rp(ASK, skill_name)

    monkeypatch.setattr(checker, "check", fake_check)
    monkeypatch.setattr(
        "lazyclaw.runtime.agent_mode.get_agent_mode",
        lambda config, user_id: _async_return(AgentMode.AUTO),
    )

    out = await checker.check_effective("u4", "send_email")
    assert out.level == ALLOW  # AUTO promoted ASK → ALLOW


def _async_return(value):
    async def _coro(*a, **k):
        return value

    return _coro()
