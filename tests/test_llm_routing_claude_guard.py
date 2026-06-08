"""CLAUDE-mode routing guard (2026-06-03).

A raw ``LLMRouter`` built by a skill that bypassed EcoRouter must STILL route
through the user's subscription/SDK in CLAUDE mode — never the (deliberately
unfunded) Anthropic API key. EcoRouter's own internal transport is exempt so
its fallback can't recurse.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lazyclaw.config import Config
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.llm.router import LLMRouter
from lazyclaw.llm.eco_router import EcoRouter


class _FakeEco:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, messages, user_id=None, model=None, role=None, **kwargs):
        self.calls.append(
            {"user_id": user_id, "model": model, "role": role, "kwargs": kwargs}
        )
        return "ECO_SENTINEL"


class _FakeProvider:
    async def chat(self, messages, model, **kwargs):
        return "API_SENTINEL"


@pytest.mark.asyncio
async def test_raw_router_reroutes_to_eco_in_claude_mode(monkeypatch):
    router = LLMRouter(Config())

    async def _claude(_uid):
        return True

    monkeypatch.setattr(router, "_is_claude_mode", _claude)
    fake = _FakeEco()
    monkeypatch.setattr(router, "_eco_router", lambda: fake)

    async def _boom(*a, **k):
        raise AssertionError("API key path must NOT run in CLAUDE mode")

    monkeypatch.setattr(router, "_resolve_api_key", _boom)

    out = await router.chat(
        [LLMMessage(role="user", content="hi")],
        model="claude-sonnet-4-6", user_id="u1",
    )
    assert out == "ECO_SENTINEL"
    assert fake.calls == [
        {"user_id": "u1", "model": "claude-sonnet-4-6", "role": "worker", "kwargs": {}}
    ]


@pytest.mark.asyncio
async def test_eco_managed_router_skips_reroute(monkeypatch):
    """EcoRouter's internal transport must hit the real provider, not loop."""
    router = LLMRouter(Config())
    router._eco_managed = True

    async def _key(_name, _uid):
        return "k"

    monkeypatch.setattr(router, "_resolve_api_key", _key)
    monkeypatch.setattr(router, "_create_provider", lambda name, key: _FakeProvider())

    def _no_eco():
        raise AssertionError("eco-managed router must not build an EcoRouter")

    monkeypatch.setattr(router, "_eco_router", _no_eco)

    out = await router.chat(
        [LLMMessage(role="user", content="hi")],
        model="claude-sonnet-4-6", user_id="u1",
    )
    assert out == "API_SENTINEL"


@pytest.mark.asyncio
async def test_non_claude_mode_uses_provider(monkeypatch):
    router = LLMRouter(Config())

    async def _not_claude(_uid):
        return False

    monkeypatch.setattr(router, "_is_claude_mode", _not_claude)

    async def _key(_name, _uid):
        return "k"

    monkeypatch.setattr(router, "_resolve_api_key", _key)
    monkeypatch.setattr(router, "_create_provider", lambda name, key: _FakeProvider())

    out = await router.chat(
        [LLMMessage(role="user", content="hi")],
        model="claude-sonnet-4-6", user_id="u1",
    )
    assert out == "API_SENTINEL"


def test_eco_init_marks_paid_router_managed():
    paid = LLMRouter(Config())
    assert paid._eco_managed is False
    EcoRouter(Config(), paid)
    assert paid._eco_managed is True


@pytest.mark.asyncio
async def test_is_claude_mode_reads_settings(monkeypatch):
    import lazyclaw.llm.eco_router as eco_mod

    router = LLMRouter(Config())

    async def _load_claude(_config, _uid):
        return SimpleNamespace(mode="claude")

    monkeypatch.setattr(eco_mod, "_load_eco_settings", _load_claude)
    assert await router._is_claude_mode("u1") is True

    async def _load_hybrid(_config, _uid):
        return SimpleNamespace(mode="hybrid")

    monkeypatch.setattr(eco_mod, "_load_eco_settings", _load_hybrid)
    assert await router._is_claude_mode("u1") is False

    # No user_id → never reroute (can't look up a user's mode).
    assert await router._is_claude_mode(None) is False
