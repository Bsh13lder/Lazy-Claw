"""Tests for the 1-hop graph expansion + RRF fusion path in
``lazyclaw.lazybrain.ask``.

Why this matters: vector-only retrieval leaves ~20-25% recall on the table
for associative queries (e.g. "what did I learn about X" where X is only
linked to the answer note via a wikilink). The graph-expansion step seeds
on the top-5 vector hits, walks 1 hop in the wikilink graph, and fuses
both ranklists via RRF. These tests pin:

  - The escape hatch (``LAZYBRAIN_GRAPH_EXPAND=0``) — must reproduce pure
    semantic_search ordering for users who don't want the expansion.
  - The lift: graph-only notes (no dense vector match) surface in the
    final ranking when wikilink-connected to top vector hits.
  - Graceful degradation when the graph has no edges or
    ``graph.get_neighbors`` raises (handles pre-migration / missing index
    state per the parallel schema agent's rollout).

All external IO is mocked — no DB, no Ollama, no LLM.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lazyclaw.lazybrain import ask


# ─── Fixtures ─────────────────────────────────────────────────────────────


class _StubConfig:
    """Minimal Config stand-in — ask_notes only forwards it to mocked deps."""


@pytest.fixture
def config() -> _StubConfig:
    return _StubConfig()


@pytest.fixture
def user_id() -> str:
    return "user-test-1"


def _note(nid: str, title: str | None = None, content: str = "body") -> dict:
    return {
        "id": nid,
        "title": title or f"Note {nid}",
        "content": content,
    }


@pytest.fixture
def patch_brain_and_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the reranker + LLM router so tests stay deterministic.

    - Rerank disabled → ``_expand_with_graph`` output flows straight to
      the LLM prompt, which is what we inspect via ``sources``.
    - LLM router imports are pre-registered as stub modules in
      ``sys.modules`` so the function-local ``from lazyclaw.llm.…`` lines
      inside ``ask_notes`` don't pull in the real ``openai`` dependency
      (which may not be installed in CI). The stubbed ``EcoRouter.chat``
      returns a canned response so the happy path runs end-to-end.
    """
    import sys
    import types

    from lazyclaw.lazybrain import rerank as _rerank

    async def _is_enabled(*_a: Any, **_kw: Any) -> bool:
        return False

    monkeypatch.setattr(_rerank, "is_enabled", _is_enabled)

    # ── Stub lazyclaw.llm.providers.base.LLMMessage ───────────────────
    base_mod = types.ModuleType("lazyclaw.llm.providers.base")

    class LLMMessage:
        def __init__(self, role: str, content: str) -> None:
            self.role = role
            self.content = content

    base_mod.LLMMessage = LLMMessage  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lazyclaw.llm.providers.base", base_mod)

    # ── Stub lazyclaw.llm.router.LLMRouter ────────────────────────────
    router_mod = types.ModuleType("lazyclaw.llm.router")

    class LLMRouter:
        def __init__(self, _config: Any) -> None:
            self.config = _config

    router_mod.LLMRouter = LLMRouter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lazyclaw.llm.router", router_mod)

    # ── Stub lazyclaw.llm.eco_router.{EcoRouter, ROLE_BRAIN} ──────────
    eco_mod = types.ModuleType("lazyclaw.llm.eco_router")

    class _Resp:
        content = "stub answer"

    class EcoRouter:
        def __init__(self, _config: Any, _router: Any) -> None:
            pass

        async def chat(self, *_a: Any, **_kw: Any) -> _Resp:
            return _Resp()

    eco_mod.EcoRouter = EcoRouter  # type: ignore[attr-defined]
    eco_mod.ROLE_BRAIN = "brain"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lazyclaw.llm.eco_router", eco_mod)


# ─── Test 1: escape hatch (LAZYBRAIN_GRAPH_EXPAND=0) ──────────────────────


@pytest.mark.asyncio
async def test_vector_only_baseline(
    monkeypatch: pytest.MonkeyPatch,
    config: _StubConfig,
    user_id: str,
    patch_brain_and_rerank: None,
) -> None:
    """With the flag off, sources MUST equal pure semantic_search order.

    No graph call, no RRF reshuffle. This is the regression gate for the
    "user wants escape hatch" memory rule.
    """
    monkeypatch.setenv("LAZYBRAIN_GRAPH_EXPAND", "0")

    vector_hits = [_note(f"n{i}", title=f"Vec {i}") for i in range(5)]

    async def _stub_search(*_a: Any, **_kw: Any) -> dict:
        return {
            "query": "q",
            "results": vector_hits,
            "source": "hybrid",
        }

    monkeypatch.setattr(ask.embeddings, "semantic_search", _stub_search)

    # Graph + store should NEVER be called when flag is off — assertion
    # via a sentinel mock that raises on access.
    from lazyclaw.lazybrain import graph as _graph

    boom = AsyncMock(side_effect=AssertionError("graph must not be called"))
    monkeypatch.setattr(_graph, "get_neighbors", boom)

    out = await ask.ask_notes(config, user_id, "what is this?")

    assert out["sources"] == [n["title"] for n in vector_hits]
    assert "graph" not in out["retrieval_source"]
    boom.assert_not_called()


# ─── Test 2: graph expansion surfaces non-top-K notes ─────────────────────


@pytest.mark.asyncio
async def test_graph_expansion_adds_neighbour_notes(
    monkeypatch: pytest.MonkeyPatch,
    config: _StubConfig,
    user_id: str,
    patch_brain_and_rerank: None,
) -> None:
    """A wikilink-only neighbour of a top vector hit MUST appear in the
    fused output even though it never scored in the vector list.

    This is the load-bearing assertion: vector recall is augmented by
    the wikilink graph. Without this, the feature has no value.
    """
    monkeypatch.setenv("LAZYBRAIN_GRAPH_EXPAND", "1")

    vector_hits = [
        _note("v1", title="Vec One"),
        _note("v2", title="Vec Two"),
    ]
    # ``g1`` is wikilink-connected to ``v1`` but has no embedding match.
    # ``g2`` is connected to ``v2``. Both must surface.
    neighbour_hit = {"g1": _note("g1", title="Graph One"),
                     "g2": _note("g2", title="Graph Two")}

    async def _stub_search(*_a: Any, **_kw: Any) -> dict:
        return {"query": "q", "results": vector_hits, "source": "hybrid"}

    async def _stub_neighbors(_cfg: Any, _uid: str, seed: str, **_kw: Any) -> dict:
        if seed == "v1":
            return {"nodes": [{"id": "g1"}], "edges": []}
        if seed == "v2":
            return {"nodes": [{"id": "g2"}], "edges": []}
        return {"nodes": [], "edges": []}

    async def _stub_get_note(_cfg: Any, _uid: str, nid: str) -> dict | None:
        return neighbour_hit.get(nid)

    from lazyclaw.lazybrain import graph as _graph
    from lazyclaw.lazybrain import store as _store

    monkeypatch.setattr(ask.embeddings, "semantic_search", _stub_search)
    monkeypatch.setattr(_graph, "get_neighbors", _stub_neighbors)
    monkeypatch.setattr(_store, "get_note", _stub_get_note)

    out = await ask.ask_notes(config, user_id, "associative query")

    titles = out["sources"]
    # Both vector seeds present
    assert "Vec One" in titles
    assert "Vec Two" in titles
    # AND at least one graph-only neighbour surfaced (the lift)
    assert "Graph One" in titles or "Graph Two" in titles
    # Retrieval source reflects the graph contribution
    assert "graph" in out["retrieval_source"]


# ─── Test 3: empty graph degrades cleanly ─────────────────────────────────


@pytest.mark.asyncio
async def test_empty_graph_falls_through_to_vector_only(
    monkeypatch: pytest.MonkeyPatch,
    config: _StubConfig,
    user_id: str,
    patch_brain_and_rerank: None,
) -> None:
    """Top-K seeds with no wikilinks → no crash, vector ordering preserved.

    Covers the "user has notes but hasn't built up a wikilink graph yet"
    cold-start case. The feature must be invisible in that state.
    """
    monkeypatch.setenv("LAZYBRAIN_GRAPH_EXPAND", "1")

    vector_hits = [_note(f"n{i}", title=f"Vec {i}") for i in range(3)]

    async def _stub_search(*_a: Any, **_kw: Any) -> dict:
        return {"query": "q", "results": vector_hits, "source": "hybrid"}

    async def _stub_neighbors(*_a: Any, **_kw: Any) -> dict:
        return {"nodes": [], "edges": []}  # graph empty

    from lazyclaw.lazybrain import graph as _graph

    monkeypatch.setattr(ask.embeddings, "semantic_search", _stub_search)
    monkeypatch.setattr(_graph, "get_neighbors", _stub_neighbors)

    out = await ask.ask_notes(config, user_id, "anything")

    assert out["sources"] == [n["title"] for n in vector_hits]
    # No fusion happened → tag should NOT include "+graph"
    assert "graph" not in out["retrieval_source"]


# ─── Test 4: graph.get_neighbors raising → fall back + warn ───────────────


@pytest.mark.asyncio
async def test_neighbors_raises_falls_through_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    config: _StubConfig,
    user_id: str,
    patch_brain_and_rerank: None,
) -> None:
    """Pre-migration / missing-index state must NOT break /ask.

    Per task spec: works BEFORE and AFTER the parallel schema agent ships
    ``idx_note_links_to_note``. The contract is the try/except wrap +
    warning log.
    """
    monkeypatch.setenv("LAZYBRAIN_GRAPH_EXPAND", "1")
    caplog.set_level(logging.WARNING, logger="lazyclaw.lazybrain.ask")

    vector_hits = [_note(f"n{i}", title=f"Vec {i}") for i in range(2)]

    async def _stub_search(*_a: Any, **_kw: Any) -> dict:
        return {"query": "q", "results": vector_hits, "source": "hybrid"}

    async def _stub_neighbors(*_a: Any, **_kw: Any) -> dict:
        raise RuntimeError("no such index: idx_note_links_to_note")

    from lazyclaw.lazybrain import graph as _graph

    monkeypatch.setattr(ask.embeddings, "semantic_search", _stub_search)
    monkeypatch.setattr(_graph, "get_neighbors", _stub_neighbors)

    out = await ask.ask_notes(config, user_id, "anything")

    # Vector ordering survives unmolested
    assert out["sources"] == [n["title"] for n in vector_hits]
    assert "graph" not in out["retrieval_source"]
    # And the failure was surfaced (so ops can see when the index is missing)
    assert any(
        "graph expansion failed" in rec.message
        for rec in caplog.records
    ), f"expected warning, got: {[r.message for r in caplog.records]}"


# ─── Test 5: env flag accepts the documented "off" tokens ─────────────────


@pytest.mark.parametrize("token", ["0", "false", "no", "off", "OFF", "False"])
def test_graph_expand_off_tokens(
    monkeypatch: pytest.MonkeyPatch, token: str,
) -> None:
    """Tokens that must disable the expansion. Documents the contract for
    config-by-env users."""
    monkeypatch.setenv("LAZYBRAIN_GRAPH_EXPAND", token)
    assert ask._graph_expand_enabled() is False


@pytest.mark.parametrize("token", ["1", "true", "yes", "on", "anything-else"])
def test_graph_expand_on_tokens(
    monkeypatch: pytest.MonkeyPatch, token: str,
) -> None:
    """Anything not in the off-set is treated as ON (fail-open)."""
    monkeypatch.setenv("LAZYBRAIN_GRAPH_EXPAND", token)
    assert ask._graph_expand_enabled() is True


def test_graph_expand_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env → ON by default. Pins the rollout-default contract."""
    monkeypatch.delenv("LAZYBRAIN_GRAPH_EXPAND", raising=False)
    assert ask._graph_expand_enabled() is True
