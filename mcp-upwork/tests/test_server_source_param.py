"""Test that the upwork_search_jobs MCP wrapper exposes and forwards
the `source` param to JobSearchParams.

Regression coverage for: wrapper used to omit `source` so the LLM
(and callers like lazyclaw's SearchJobsSkill) couldn't request the
personalized best-matches feed, defaulting every search to keyword
mode against the global job board.
"""

from __future__ import annotations

import inspect

import pytest

from upwork_mcp.server import upwork_search_jobs
from upwork_mcp.tools.jobs import JobSearchParams


# ---------------------------------------------------------------------------
# Wrapper signature
# ---------------------------------------------------------------------------

def test_wrapper_exposes_source_param():
    """The MCP-exposed tool must advertise `source` so the LLM can pass it."""
    sig = inspect.signature(upwork_search_jobs)
    assert "source" in sig.parameters


def test_wrapper_defaults_source_to_best_matches():
    """Wrapper default flips the historic 'search' default so direct LLM
    calls also get profile-matched results."""
    sig = inspect.signature(upwork_search_jobs)
    assert sig.parameters["source"].default == "best_matches"


def test_wrapper_query_default_is_empty_string():
    """Best-matches doesn't need a query, so query is optional."""
    sig = inspect.signature(upwork_search_jobs)
    assert sig.parameters["query"].default == ""


# ---------------------------------------------------------------------------
# Wrapper → JobSearchParams passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wrapper_passes_source_through(monkeypatch):
    """Calling the wrapper with source='best_matches' must propagate to
    JobSearchParams.source — proves the new param is actually wired."""
    captured: dict = {}

    async def fake_search_jobs(params: JobSearchParams):
        captured["params"] = params
        return []

    # Patch the symbol the wrapper imports as `search_jobs`.
    monkeypatch.setattr("upwork_mcp.server.search_jobs", fake_search_jobs)

    await upwork_search_jobs(query="", source="best_matches")
    assert captured["params"].source == "best_matches"
    assert (captured["params"].query or "") == ""


@pytest.mark.asyncio
async def test_wrapper_search_source_forwards_query(monkeypatch):
    """source='search' + non-empty query → both forwarded verbatim."""
    captured: dict = {}

    async def fake_search_jobs(params: JobSearchParams):
        captured["params"] = params
        return []

    monkeypatch.setattr("upwork_mcp.server.search_jobs", fake_search_jobs)

    await upwork_search_jobs(query="python scraping", source="search")
    assert captured["params"].source == "search"
    assert captured["params"].query == "python scraping"


@pytest.mark.asyncio
async def test_wrapper_default_invocation_uses_best_matches(monkeypatch):
    """Bare call with no args → best_matches (the new default)."""
    captured: dict = {}

    async def fake_search_jobs(params: JobSearchParams):
        captured["params"] = params
        return []

    monkeypatch.setattr("upwork_mcp.server.search_jobs", fake_search_jobs)

    await upwork_search_jobs()
    assert captured["params"].source == "best_matches"
