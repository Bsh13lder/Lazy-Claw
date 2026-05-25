"""Lock in the Ollama embed contract: /api/embed + input + embeddings[][].

These tests prevent the regression that drove 540 HTTP 500s/hour pre-fix:
the legacy /api/embeddings endpoint silently ignored ``truncate=true`` on
Ollama 0.20.x, so any note past nomic-bert's 2048-token hard limit
returned ``HTTP 500 {"error":"the input length exceeds the context
length"}``. The new ``/api/embed`` endpoint truncates server-side, so we
switched the request URL + body shape (`prompt` → `input`) + response
parser (`embedding` → `embeddings[0]`).

We mock httpx so the suite runs without an Ollama daemon present.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from lazyclaw.lazybrain import embeddings as embed_mod
from lazyclaw.lazybrain.embeddings import EMBED_DIM, EMBED_MODEL, _ollama_embed


# ── Fake httpx response + AsyncClient ──────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None,
                 text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or (str(payload) if payload else "")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if 400 <= self.status_code:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )


class _RecordingClient:
    """Capture every POST so tests can assert URL + body shape."""

    calls: list[dict[str, Any]] = []
    responses: list[_FakeResponse] = []

    def __init__(self, *args: Any, base_url: str = "", timeout: int = 30,
                 **kwargs: Any) -> None:
        self.base_url = base_url

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        _RecordingClient.calls.append({"url": url, "json": json})
        # Pop the next scripted response, or fall back to a success vector.
        if _RecordingClient.responses:
            return _RecordingClient.responses.pop(0)
        return _FakeResponse(
            200,
            {"model": EMBED_MODEL, "embeddings": [[0.01] * EMBED_DIM]},
        )


@pytest.fixture(autouse=True)
def _reset_recorder():
    _RecordingClient.calls = []
    _RecordingClient.responses = []
    # Re-arm the one-time context-overflow latch so each test sees a
    # fresh module state.
    embed_mod._CTX_OVERFLOW_LOGGED = False
    # Re-arm the one-time client-reject (4xx) latch too — otherwise the
    # latch survives test order and silently flips the WARNING expectation.
    embed_mod._CLIENT_REJECT_LOGGED = False
    yield


# ── Tests ──────────────────────────────────────────────────────────────


def test_ollama_embed_posts_to_new_endpoint_with_input_key():
    """Lock the URL + request body shape against silent endpoint regression."""
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("hello world"))

    assert vec is not None
    assert len(vec) == EMBED_DIM
    assert len(_RecordingClient.calls) == 1
    call = _RecordingClient.calls[0]
    # The fix: new endpoint path, new body key.
    assert call["url"] == "/api/embed", (
        f"must use /api/embed (legacy /api/embeddings ignored truncate=true "
        f"and 500'd on long notes), got {call['url']!r}"
    )
    body = call["json"]
    assert body["model"] == EMBED_MODEL
    assert "input" in body, "new endpoint uses `input`, not `prompt`"
    assert "prompt" not in body, "legacy `prompt` key must not be sent"
    # truncate is on the wire as a belt-and-suspenders guard against
    # future Ollama default flips.
    assert body.get("truncate") is True


def test_ollama_embed_parses_plural_embeddings_response():
    """/api/embed returns ``{"embeddings": [[...]]}`` — extract row 0."""
    expected = [0.5] * EMBED_DIM
    _RecordingClient.responses = [
        _FakeResponse(200, {"model": EMBED_MODEL, "embeddings": [expected]}),
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("payload"))
    assert vec == expected


def test_ollama_embed_falls_back_to_legacy_embedding_shape():
    """If users pin an older Ollama that still returns ``embedding`` (singular),
    we still extract a vector — avoids breaking sites mid-upgrade."""
    expected = [0.25] * EMBED_DIM
    _RecordingClient.responses = [
        _FakeResponse(200, {"embedding": expected}),
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("payload"))
    assert vec == expected


def test_ollama_embed_truncates_input_to_char_cap():
    """The 6 KB cap is belt-and-suspenders. Server-side truncate is the
    authoritative guard, but we cap on the client so a chunker bug can't
    drown the GPU."""
    huge = "x" * 100_000
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        asyncio.run(_ollama_embed(huge))
    assert len(_RecordingClient.calls) == 1
    sent = _RecordingClient.calls[0]["json"]["input"]
    assert len(sent) == embed_mod._EMBED_CHAR_CAP
    assert len(sent) < len(huge)


def test_ollama_embed_returns_none_on_404_and_does_not_retry():
    """Model missing → one call, no retry, returns None — matches the
    'ollama pull nomic-embed-text' UX."""
    _RecordingClient.responses = [_FakeResponse(404, {"error": "model not found"})]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("payload"))
    assert vec is None
    assert len(_RecordingClient.calls) == 1


def test_ollama_embed_retries_once_on_5xx_then_gives_up():
    """One retry on transient 5xx, then bail. Two scripted 500s → 2 calls
    total → None returned."""
    _RecordingClient.responses = [
        _FakeResponse(500, text='{"error":"transient"}'),
        _FakeResponse(500, text='{"error":"still transient"}'),
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("payload"))
    assert vec is None
    assert len(_RecordingClient.calls) == 2


def test_ollama_embed_escalates_context_length_500_to_warning_once(caplog):
    """Persistent 500 with ``context length`` body → WARNING (once per
    process). Other 5xx stay at DEBUG so we don't flood logs."""
    import logging
    caplog.set_level(logging.WARNING, logger="lazyclaw.lazybrain.embeddings")
    body = '{"error":"the input length exceeds the context length"}'
    _RecordingClient.responses = [
        _FakeResponse(500, text=body),
        _FakeResponse(500, text=body),
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        asyncio.run(_ollama_embed("a" * 10_000))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "context length" in warnings[0].getMessage().lower()

    # A second call with the same failure must NOT emit a second WARNING
    # — the one-time latch keeps the log floor clean.
    caplog.clear()
    _RecordingClient.responses = [
        _FakeResponse(500, text=body),
        _FakeResponse(500, text=body),
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        asyncio.run(_ollama_embed("b" * 10_000))
    warnings2 = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings2 == []


def test_ollama_embed_skips_empty_and_whitespace_input():
    """Empty or whitespace-only input short-circuits before the HTTP call —
    Ollama returns `{"embedding":[]}` for empty input, which would
    needlessly burn a request and a GPU slot."""
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        assert asyncio.run(_ollama_embed("")) is None
        assert asyncio.run(_ollama_embed("   \n\t")) is None
    assert _RecordingClient.calls == []


def test_ollama_embed_400_logs_body_once_at_warning(caplog):
    """4xx from Ollama → log body + input preview at WARNING the FIRST
    time, DEBUG thereafter.

    Pre-fix: ``resp.raise_for_status()`` raised an HTTPStatusError that
    landed in the outer ``except Exception`` and logged at DEBUG with no
    body. We saw "3 of 10 dirty notes 400 every cycle" for hours with
    zero signal on which 3 notes or why. Now the first 4xx in the
    process surfaces the response body + input shape so the cause shows
    up exactly once; the cooldown already gates retry rate.
    """
    import logging
    caplog.set_level(logging.DEBUG, logger="lazyclaw.lazybrain.embeddings")
    body = '{"error":"invalid input type"}'
    _RecordingClient.responses = [_FakeResponse(400, text=body)]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("first reject text"))
    assert vec is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "invalid input type" in msg
    assert "400" in msg
    # Input preview surfaces so we know which note tripped the reject.
    assert "first reject text" in msg

    # Second 4xx in the same process must NOT emit a second WARNING.
    caplog.clear()
    _RecordingClient.responses = [_FakeResponse(400, text=body)]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec2 = asyncio.run(_ollama_embed("second reject text"))
    assert vec2 is None
    warnings2 = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings2 == []
    # And a DEBUG line is still emitted so deep debugging stays possible.
    debugs = [
        r for r in caplog.records
        if r.levelname == "DEBUG" and "client reject" in r.getMessage()
    ]
    assert len(debugs) >= 1


def test_ollama_embed_400_returns_none():
    """Existing contract: any failure (4xx included) returns None — the
    caller (``upsert_embedding``) treats None as 'skip this note, don't
    persist a corrupt centroid'. The new WARNING log MUST NOT change
    the return value."""
    _RecordingClient.responses = [
        _FakeResponse(400, text='{"error":"invalid input type"}'),
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("payload"))
    assert vec is None
    # Single attempt — 4xx must not trigger the 5xx retry path.
    assert len(_RecordingClient.calls) == 1


def test_ollama_embed_rejects_wrong_dim_response():
    """If Ollama returns a vector with the wrong dim (e.g. wrong model
    loaded), drop it so we never persist a corrupt centroid."""
    _RecordingClient.responses = [
        _FakeResponse(200, {"embeddings": [[0.0] * 512]}),  # wrong dim
    ]
    with patch("lazyclaw.lazybrain.embeddings.httpx.AsyncClient", _RecordingClient):
        vec = asyncio.run(_ollama_embed("payload"))
    assert vec is None
