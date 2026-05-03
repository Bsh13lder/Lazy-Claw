"""web_search chain pinned: Brave Search API → mcp-scraper → DuckDuckGo.

Rewrite of 2026-05-02 — Serper / SerpAPI deleted. Brave became the primary
provider (free 2,000/mo, clean curated index, cleaner snippets than Google
scrape). Price-class queries (flights, shopping, "cheapest", etc.) skip
search entirely and return a browser-instruction string.

These tests pin the contract — if a future change reorders the chain or
reintroduces a paid provider as default, they'll fail loudly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.skills.builtin.web_search import (
    WebSearchSkill,
    _provider_cooldowns,
    _is_price_query,
    _canonical_live_url,
    set_active_provider,
)

_POOL_SEARCH_NAME = "mcp_scraper_search_google"


class _FakeScraperSkill:
    """Stand-in for an MCPToolSkill that returns canned search markdown."""

    def __init__(self, return_value: str = "1. Result A\n2. Result B"):
        self._return_value = return_value
        self.calls: list[dict] = []

    async def execute(self, user_id: str, params: dict) -> str:
        self.calls.append({"user_id": user_id, "params": params})
        return self._return_value


class _FakeRegistry:
    def __init__(self, scraper_skill: _FakeScraperSkill | None):
        self._scraper = scraper_skill
        self._tool_name = _POOL_SEARCH_NAME

    def get(self, name: str):
        if name == self._tool_name:
            return self._scraper
        return None


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with no cooldowns and the default provider."""
    _provider_cooldowns.clear()
    set_active_provider("auto")
    yield
    _provider_cooldowns.clear()
    set_active_provider("auto")


# ── Brave Search API: primary path ─────────────────────────────────────

@pytest.mark.asyncio
async def test_brave_runs_first_when_key_configured(monkeypatch):
    """When BRAVE_KEY is set, Brave is tried before scraper / DDG."""
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))
    monkeypatch.setenv("BRAVE_KEY", "test-key")

    with (
        patch(
            "lazyclaw.skills.builtin.web_search._brave_search",
            new=AsyncMock(return_value="1. Brave hit\n   https://x.com\n   snippet"),
        ) as brave,
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback", new=AsyncMock(),
        ) as ddg,
    ):
        result = await skill.execute(
            user_id="u1", params={"query": "find me restaurants"},
        )

    assert brave.called, "Brave should fire first"
    assert len(fake.calls) == 0, "Scraper must not run when Brave succeeds"
    assert not ddg.called, "DDG must not run when Brave succeeds"
    assert "[Brave Search" in result


@pytest.mark.asyncio
async def test_falls_back_to_scraper_when_brave_key_missing(monkeypatch):
    """No Brave key → Brave is skipped, scraper runs.

    Patches ``_resolve_brave_key`` directly because ``load_config()``
    re-reads .env with override=True — a plain ``monkeypatch.delenv``
    would be undone the moment the resolver runs.
    """
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch(
            "lazyclaw.skills.builtin.web_search._resolve_brave_key",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "lazyclaw.skills.builtin.web_search._brave_search",
            new=AsyncMock(return_value=""),  # treated as not configured
        ) as brave,
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback", new=AsyncMock(),
        ) as ddg,
    ):
        result = await skill.execute(
            user_id="u1", params={"query": "find me restaurants"},
        )

    assert "[mcp-scraper" in result
    assert len(fake.calls) == 1
    assert fake.calls[0]["params"]["request"]["query"] == "find me restaurants"
    assert not ddg.called, "DDG must only run as last resort"


@pytest.mark.asyncio
async def test_user_pinned_scraper_skips_brave(monkeypatch):
    """User explicitly picked 'scraper' → Brave is bypassed.

    Patches ``resolve_active_provider`` directly because the real one
    reads ``users.settings.general.search_provider`` from the DB which
    defaults to "auto" and would override the global default.
    """
    monkeypatch.setenv("BRAVE_KEY", "test-key")
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch(
            "lazyclaw.skills.builtin.web_search.resolve_active_provider",
            new=AsyncMock(return_value="scraper"),
        ),
        patch(
            "lazyclaw.skills.builtin.web_search._brave_search",
            new=AsyncMock(return_value="should not be called"),
        ) as brave,
    ):
        result = await skill.execute("u1", {"query": "anything"})

    assert not brave.called, "Brave must not run when user pinned scraper"
    assert len(fake.calls) == 1
    assert "[mcp-scraper" in result


@pytest.mark.asyncio
async def test_user_pinned_duckduckgo_skips_others(monkeypatch):
    """User picked DDG → no Brave, no scraper."""
    monkeypatch.setenv("BRAVE_KEY", "test-key")
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch(
            "lazyclaw.skills.builtin.web_search.resolve_active_provider",
            new=AsyncMock(return_value="duckduckgo"),
        ),
        patch(
            "lazyclaw.skills.builtin.web_search._brave_search", new=AsyncMock(),
        ) as brave,
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback",
            new=AsyncMock(return_value="ddg results"),
        ) as ddg,
    ):
        result = await skill.execute("u1", {"query": "anything"})

    assert not brave.called
    assert len(fake.calls) == 0
    assert ddg.called
    assert "DuckDuckGo" in result


@pytest.mark.asyncio
async def test_scraper_failure_trips_cooldown_and_falls_to_ddg(monkeypatch):
    """When scraper returns a 429 marker, cooldown trips and DDG runs.

    Auto-mode: no Brave key (we patch the resolver directly because
    ``load_config()`` re-reads .env with override=True, which would
    undo a plain ``monkeypatch.delenv`` whenever the resolver runs).
    Scraper hits the rate-limit marker, caller falls through to DDG.
    """
    fake = _FakeScraperSkill(return_value="rate limit hit, 429")
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch(
            "lazyclaw.skills.builtin.web_search._resolve_brave_key",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback",
            new=AsyncMock(return_value="ddg result"),
        ),
    ):
        result = await skill.execute("u1", {"query": "anything"})

    assert "scraper" in _provider_cooldowns
    assert "DuckDuckGo" in result


# ── Price-query routing: search bypassed, browser instruction returned ──

def test_price_query_classifier_flags_flight_keywords():
    assert _is_price_query("cheapest flight from BCN to TBS June 15", "search")
    assert _is_price_query("flight from Madrid to Tokyo", "search")
    assert _is_price_query("how much does an iPhone 16 cost", "search")
    assert _is_price_query("any iPhone 16 in stock at Apple Store", "search")
    assert _is_price_query("hotel near Barcelona", "search")


def test_price_query_classifier_skips_factual_queries():
    assert not _is_price_query("what is the capital of France", "search")
    assert not _is_price_query("python dataclass example", "search")
    assert not _is_price_query("how to bake bread", "search")


def test_canonical_url_for_flights_uses_google_flights():
    url = _canonical_live_url("flight from BCN to TBS on 2026-06-15", "flights")
    assert "google.com/travel/flights" in url
    assert "BCN" in url and "TBS" in url
    assert "2026-06-15" in url


def test_canonical_url_for_shopping_uses_google_shopping():
    url = _canonical_live_url("cheapest iPhone 16 Madrid", "shopping")
    assert "tbm=shop" in url


@pytest.mark.asyncio
async def test_price_query_returns_browser_instruction_not_search(monkeypatch):
    """A flight/shopping query must NOT hit any search backend.

    Search APIs return cached snippets which lie about prices. The skill
    must return a structured `[PRICE_QUERY]` instruction with a canonical
    URL the brain can open in the browser.
    """
    monkeypatch.setenv("BRAVE_KEY", "test-key")
    fake = _FakeScraperSkill()
    skill = WebSearchSkill(registry=_FakeRegistry(fake))

    with (
        patch(
            "lazyclaw.skills.builtin.web_search._brave_search", new=AsyncMock(),
        ) as brave,
        patch(
            "lazyclaw.skills.builtin.web_search._ddg_fallback", new=AsyncMock(),
        ) as ddg,
    ):
        result = await skill.execute(
            "u1",
            {"query": "cheapest flight from BCN to TBS on 2026-06-15"},
        )

    assert "[PRICE_QUERY" in result
    assert "google.com/travel/flights" in result
    assert "browser(" in result
    assert not brave.called, "Brave must not run for price queries"
    assert len(fake.calls) == 0, "Scraper must not run for price queries"
    assert not ddg.called, "DDG must not run for price queries"
