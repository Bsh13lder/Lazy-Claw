"""Tests for WebSocket chat Origin validation (CSWSH defense).

The session cookie is ``samesite=lax``, which does NOT block cross-site
WebSocket handshakes. Without an Origin check a malicious page could open
``wss://.../ws/chat`` carrying the victim's cookie and drive the agent.

Policy guarded here (``chat_ws._origin_allowed`` / ``_authenticate_ws``):
- Origin PRESENT + mismatched → REJECT.
- Origin PRESENT + matches configured cors_origin → ALLOW.
- Origin ABSENT → ALLOW (native / non-browser clients omit it).
- Cookie auth stays intact (a rejected Origin never reaches the session
  lookup; a matching Origin still requires a valid session cookie).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

import lazyclaw.gateway.routes.chat_ws as chat_ws


class _FakeWS:
    """Stand-in for ``starlette.websockets.WebSocket``.

    ``_origin_allowed`` reads ``ws.headers``; ``_authenticate_ws`` also
    reads ``ws.cookies``.
    """

    def __init__(self, origin: str | None = None, cookies: dict | None = None):
        hdrs = {"origin": origin} if origin is not None else {}
        self.headers = Headers(hdrs)
        self.cookies = cookies or {}


@pytest.fixture
def configured_origin(monkeypatch):
    """Pin the gateway cors_origin the WS check reads."""
    monkeypatch.setattr(
        chat_ws._config, "cors_origin", "https://app.example.com", raising=False
    )
    return "https://app.example.com"


# ── Pure Origin-policy tests (no session lookup) ────────────────────────

def test_rejects_present_mismatched_origin(configured_origin):
    ws = _FakeWS(origin="https://evil.example.com")
    assert chat_ws._origin_allowed(ws) is False


def test_allows_matching_origin(configured_origin):
    ws = _FakeWS(origin="https://app.example.com")
    assert chat_ws._origin_allowed(ws) is True


def test_allows_matching_origin_trailing_slash(monkeypatch):
    monkeypatch.setattr(
        chat_ws._config, "cors_origin", "https://app.example.com/", raising=False
    )
    ws = _FakeWS(origin="https://app.example.com")
    assert chat_ws._origin_allowed(ws) is True


def test_allows_absent_origin(configured_origin):
    ws = _FakeWS(origin=None)
    assert chat_ws._origin_allowed(ws) is True


def test_supports_comma_separated_origins(monkeypatch):
    monkeypatch.setattr(
        chat_ws._config,
        "cors_origin",
        "https://app.example.com, https://admin.example.com",
        raising=False,
    )
    assert chat_ws._origin_allowed(_FakeWS(origin="https://admin.example.com")) is True
    assert chat_ws._origin_allowed(_FakeWS(origin="https://other.example.com")) is False


# ── Full _authenticate_ws path (Origin + cookie) ────────────────────────

@pytest.fixture
def stub_session_user(monkeypatch):
    """Make get_session_user resolve any cookie to a fake user."""
    async def _fake_get_session_user(_config, session_id):
        if session_id == "good-session":
            return SimpleNamespace(id="u1", username="alice")
        return None

    import lazyclaw.gateway.auth as auth_mod
    monkeypatch.setattr(auth_mod, "get_session_user", _fake_get_session_user)


async def test_authenticate_rejects_mismatched_origin_before_cookie(
    configured_origin, stub_session_user
):
    # Even WITH a valid cookie, a bad Origin must be refused.
    ws = _FakeWS(origin="https://evil.example.com", cookies={"session_id": "good-session"})
    assert await chat_ws._authenticate_ws(ws) is None


async def test_authenticate_allows_matching_origin_with_cookie(
    configured_origin, stub_session_user
):
    ws = _FakeWS(origin="https://app.example.com", cookies={"session_id": "good-session"})
    user = await chat_ws._authenticate_ws(ws)
    assert user is not None
    assert user.username == "alice"


async def test_authenticate_allows_absent_origin_with_cookie(
    configured_origin, stub_session_user
):
    """Native client: no Origin header, valid cookie → authenticated."""
    ws = _FakeWS(origin=None, cookies={"session_id": "good-session"})
    user = await chat_ws._authenticate_ws(ws)
    assert user is not None


async def test_authenticate_still_requires_cookie_with_good_origin(
    configured_origin, stub_session_user
):
    """Matching Origin does not bypass cookie auth."""
    ws = _FakeWS(origin="https://app.example.com", cookies={})
    assert await chat_ws._authenticate_ws(ws) is None
