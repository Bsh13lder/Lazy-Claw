"""Sensitive skill-level permission defaults (2026-06-10 security audit).

Money-moving MCP tools live in the blanket-ALLOW ``mcp`` category, so
without a skill-level overlay the brain can accept a binding Upwork
contract or start a payment-release timer with zero ask-back. These
tests pin the overlay: it beats the category default, loses to an
explicit user override, and matches MCP tools by bare name (the
registry registers them as ``mcp_<server-uuid>_<tool>``).
"""

from __future__ import annotations

import pytest

from lazyclaw.permissions.checker import PermissionChecker
from lazyclaw.permissions.models import (
    ALLOW,
    ASK,
    VALID_LEVELS,
    SENSITIVE_SKILL_DEFAULTS,
)

_SERVER_UUID = "3f2a1b4c-9d8e-4f00-a1b2-c3d4e5f60789"


class _FakeSkill:
    def __init__(self, category: str) -> None:
        self.category = category


class _FakeRegistry:
    """Every known skill resolves to the ``mcp`` category."""

    def __init__(self, known: dict[str, str] | None = None) -> None:
        self._known = known or {}

    def get(self, name: str) -> _FakeSkill | None:
        category = self._known.get(name)
        return _FakeSkill(category) if category else _FakeSkill("mcp")


def _make_checker(monkeypatch: pytest.MonkeyPatch, settings: dict) -> PermissionChecker:
    async def _fake_settings(config, user_id):
        return settings

    monkeypatch.setattr(
        "lazyclaw.permissions.checker.get_permission_settings", _fake_settings
    )
    return PermissionChecker(config=None, registry=_FakeRegistry())


# ── Overlay contents ──────────────────────────────────────────────────


def test_money_moving_upwork_tools_are_listed_as_ask():
    assert SENSITIVE_SKILL_DEFAULTS["upwork_accept_offer"] == ASK
    assert SENSITIVE_SKILL_DEFAULTS["upwork_submit_milestone"] == ASK


def test_payment_skill_is_listed_as_ask():
    """Card save/retrieve must get the REAL ask-back on every channel —
    category-ASK alone was auto-approved by Telegram's callback."""
    assert SENSITIVE_SKILL_DEFAULTS["payment"] == ASK


def test_overlay_levels_are_valid():
    assert all(level in VALID_LEVELS for level in SENSITIVE_SKILL_DEFAULTS.values())


# ── Resolution behavior ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sensitive_default_beats_mcp_category_allow(monkeypatch):
    checker = _make_checker(monkeypatch, settings={})
    resolved = await checker.check("u1", "upwork_accept_offer")
    assert resolved.level == ASK
    assert resolved.source == "sensitive_default"


@pytest.mark.asyncio
async def test_sensitive_default_matches_dynamic_mcp_id_by_bare_name(monkeypatch):
    checker = _make_checker(monkeypatch, settings={})
    resolved = await checker.check(
        "u1", f"mcp_{_SERVER_UUID}_upwork_submit_milestone"
    )
    assert resolved.level == ASK
    assert resolved.source == "sensitive_default"


@pytest.mark.asyncio
async def test_user_override_beats_sensitive_default(monkeypatch):
    checker = _make_checker(
        monkeypatch,
        settings={"skill_overrides": {"upwork_accept_offer": ALLOW}},
    )
    resolved = await checker.check("u1", "upwork_accept_offer")
    assert resolved.level == ALLOW
    assert resolved.source == "skill_override"


@pytest.mark.asyncio
async def test_bare_name_override_applies_to_dynamic_mcp_id(monkeypatch):
    """/allow upwork_accept_offer must work even though the registry id
    is mcp_<uuid>_upwork_accept_offer."""
    checker = _make_checker(
        monkeypatch,
        settings={"skill_overrides": {"upwork_accept_offer": ALLOW}},
    )
    resolved = await checker.check(
        "u1", f"mcp_{_SERVER_UUID}_upwork_accept_offer"
    )
    assert resolved.level == ALLOW
    assert resolved.source == "skill_override"


@pytest.mark.asyncio
async def test_non_sensitive_mcp_tool_keeps_category_allow(monkeypatch):
    checker = _make_checker(monkeypatch, settings={})
    resolved = await checker.check("u1", "upwork_get_messages")
    assert resolved.level == ALLOW
    assert resolved.source == "category_default"


@pytest.mark.asyncio
async def test_user_category_default_does_not_beat_sensitive_default(monkeypatch):
    """A blanket 'mcp: allow' user setting must NOT silently re-open the
    money movers — only an explicit per-skill override may."""
    checker = _make_checker(
        monkeypatch,
        settings={"category_defaults": {"mcp": ALLOW}},
    )
    resolved = await checker.check("u1", "upwork_accept_offer")
    assert resolved.level == ASK
    assert resolved.source == "sensitive_default"
