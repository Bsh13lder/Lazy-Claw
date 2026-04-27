"""web_search must try mcp-scraper before SerpAPI/Serper.

User's call (2026-04-26): SerpAPI quota burns out at parallel scale; mcp-scraper's
free `search_google` should be the primary provider. SerpAPI/Serper become opt-in
fallbacks. These tests pin the contract — if a future change reintroduces SerpAPI
as the default, they'll fail.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.skills.builtin.web_search import (
    WebSearchSkill,
    _SCRAPER_SEARCH_SUFFIX,
    _SCRAPER_SERVER_NAME,
    _provider_cooldowns,
    set_active_provider,
)


class _FakeScraperSkill:
    """Stand-in for an MCPToolSkill that returns canned search markdown."""

    def __init__(self, return_value: str = "1. Result A\n2. Result B"):
        self._return_value = return_value
        self.calls: list[dict] = []

    async def execute(self, user_id: str, params: dict) -> str:
        self.calls.append({"user_id": user_id, "params": params})
        return self._return_value


class _FakeRegistry:
    """Tiny registry stub. Surfaces a single fake mcp-scraper search tool."""

    def __init__(self, scraper_skill: _FakeScraperSkill | None):
        self._scraper = scraper_skill
        self._uuid = "ce2f19a7-bd2a-4259-a669-89b316165a46"
        self._tool_name = f"mcp_{self._uuid}{_SCRAPER_SEARCH_SUFFIX}"

    def list_mcp_tools(self) -> list[dict]:
        if self._scraper is None:
            return []
        return [{
            "function": {
                "name": self._tool_name,
                "description": (
                    f"Search Google via {_SCRAPER_SERVER_NAME}. "
                    "Direct scrape, no API key."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def get(self, name: str):
        if name == self._tool_name:
            return self._scraper
        return None


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with no cooldowns and the default provider."""
    _provider_cooldowns.clear()
    set_active_provider("scraper")
    yield
    _provider_cooldowns.clear()
    set_active_provider("scraper")


@pytest.mark.asyncio
async def test_scraper_runs_first_when_available():
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch("lazyclaw.skills.builtin.web_search._serper_search", new=AsyncMock()) as serper,
        patch("lazyclaw.skills.builtin.web_search._serpapi_search", new=AsyncMock()) as serpapi,
        patch("lazyclaw.skills.builtin.web_search._ddg_fallback", new=AsyncMock()) as ddg,
    ):
        result = await skill.execute(
            user_id="u1", params={"query": "find me restaurants"},
        )

    assert "[mcp-scraper" in result
    assert len(fake.calls) == 1
    # search_google's MCP schema wraps everything in a `request` dict
    assert fake.calls[0]["params"]["request"]["query"] == "find me restaurants"
    assert fake.calls[0]["params"]["request"]["num_results"] == 5
    serper.assert_not_called()
    serpapi.assert_not_called()
    ddg.assert_not_called()


@pytest.mark.asyncio
async def test_scraper_skipped_when_user_picked_serper(monkeypatch):
    """User-picked serper still works WHEN the paid-search flag is on.

    The flag was added 2026-04-27 — paid providers stay dormant by default.
    Picking "serper" via /search is harmless without the flag (DDG fallback).
    With the flag on, the user's pick is honored and scraper is bypassed.
    """
    monkeypatch.setenv("LAZYCLAW_ENABLE_PAID_SEARCH", "1")
    set_active_provider("serper")
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    # Set serper key so the serper branch is reachable
    with (
        patch.dict("os.environ", {"SERPER_KEY": "test-key"}),
        patch(
            "lazyclaw.skills.builtin.web_search._serper_search",
            new=AsyncMock(return_value="serper hit"),
        ) as serper,
    ):
        result = await skill.execute(
            user_id="u1", params={"query": "find me restaurants"},
        )

    # Serper was called, scraper was not
    assert serper.called
    assert len(fake.calls) == 0
    assert "Serper.dev" in result


@pytest.mark.asyncio
async def test_paid_providers_dormant_when_flag_off(monkeypatch):
    """Even if user explicitly picked SerpAPI, the paid chain stays dormant
    when LAZYCLAW_ENABLE_PAID_SEARCH is unset. Falls through to DDG instead.
    """
    monkeypatch.delenv("LAZYCLAW_ENABLE_PAID_SEARCH", raising=False)
    set_active_provider("serpapi")
    skill = WebSearchSkill(registry=_FakeRegistry(None))  # no scraper

    with (
        patch.dict("os.environ", {"SERPER_KEY": "k", "SERPAPI_KEY": "k"}),
        patch(
            "lazyclaw.skills.builtin.web_search._serper_search",
            new=AsyncMock(return_value="serper hit"),
        ) as serper,
        patch(
            "lazyclaw.skills.builtin.web_search._serpapi_search",
            new=AsyncMock(return_value="serpapi hit"),
        ) as serpapi,
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback",
            new=AsyncMock(return_value="ddg result"),
        ) as ddg,
    ):
        result = await skill.execute("u1", {"query": "anything"})

    assert not serper.called, "Serper should be gated off"
    assert not serpapi.called, "SerpAPI should be gated off"
    assert ddg.called, "DDG should be the fallback when paid is disabled"
    assert "DuckDuckGo" in result


@pytest.mark.asyncio
async def test_paid_providers_active_when_flag_on(monkeypatch):
    """With LAZYCLAW_ENABLE_PAID_SEARCH=1 the paid chain runs as before."""
    monkeypatch.setenv("LAZYCLAW_ENABLE_PAID_SEARCH", "1")
    set_active_provider("serpapi")
    skill = WebSearchSkill(registry=_FakeRegistry(None))

    with (
        patch.dict("os.environ", {"SERPER_KEY": "k", "SERPAPI_KEY": "k"}),
        patch(
            "lazyclaw.skills.builtin.web_search._serpapi_search",
            new=AsyncMock(return_value="serpapi hit"),
        ) as serpapi,
    ):
        result = await skill.execute("u1", {"query": "anything"})

    assert serpapi.called, "SerpAPI should fire when flag is on"
    assert "SerpAPI" in result


@pytest.mark.asyncio
async def test_scraper_failure_trips_cooldown():
    """A scraper exception (e.g. Google CAPTCHA) flips into cooldown so we don't
    hammer it for the next minute."""
    fake = _FakeScraperSkill(return_value="rate limit hit, 429")
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback",
            new=AsyncMock(return_value="ddg result"),
        ),
        patch("lazyclaw.skills.builtin.web_search._serper_search", new=AsyncMock()),
        patch("lazyclaw.skills.builtin.web_search._serpapi_search", new=AsyncMock()),
    ):
        result = await skill.execute(
            user_id="u1", params={"query": "anything"},
        )

    # Rate-limit detected — cooldown should be tripped, fallback ran (DDG)
    assert "scraper" in _provider_cooldowns
    assert "DuckDuckGo" in result
