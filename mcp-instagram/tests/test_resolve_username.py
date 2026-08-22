"""Unit tests for _resolve_username and _username_error in mcp_instagram.server.

These tests exercise only the pure-Python resolver logic; they mock out
``instagrapi``, ``mcp``, and the session manager so the module can be
imported without the full instagrapi stack being installed.
"""
import sys
import types
from unittest.mock import MagicMock, AsyncMock
import pytest


# ---------------------------------------------------------------------------
# Bootstrap: provide lightweight stubs for heavy dependencies.
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _inject_stubs() -> None:
    """Inject minimal stubs for mcp.* and instagrapi before importing server."""
    # mcp tree
    for mod_name in ("mcp", "mcp.server", "mcp.server.stdio", "mcp.types"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _stub_module(mod_name)

    # Server instance needs decorator-compatible .list_tools() / .call_tool().
    _decorator = lambda fn: fn  # noqa: E731 — passthrough decorator
    _server_instance = MagicMock()
    _server_instance.list_tools = MagicMock(return_value=_decorator)
    _server_instance.call_tool = MagicMock(return_value=_decorator)
    _Server = MagicMock(return_value=_server_instance)

    mcp_server = sys.modules["mcp.server"]
    if not hasattr(mcp_server, "Server"):
        mcp_server.Server = _Server

    mcp_types = sys.modules["mcp.types"]
    if not hasattr(mcp_types, "TextContent"):
        # TextContent needs to behave like a namedtuple-style class with .text
        class _TC:
            def __init__(self, *, type: str = "text", text: str = ""):
                self.type = type
                self.text = text
        mcp_types.TextContent = _TC
    if not hasattr(mcp_types, "Tool"):
        mcp_types.Tool = MagicMock

    mcp_stdio = sys.modules["mcp.server.stdio"]
    if not hasattr(mcp_stdio, "stdio_server"):
        mcp_stdio.stdio_server = MagicMock

    # instagrapi stubs
    for mod_name in ("instagrapi", "instagrapi.types"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _stub_module(mod_name)
    sys.modules["instagrapi.types"].StoryMention = MagicMock

    # mcp_instagram.session_manager stub
    if "mcp_instagram" not in sys.modules:
        sys.modules["mcp_instagram"] = _stub_module("mcp_instagram")
    if "mcp_instagram.session_manager" not in sys.modules:
        sm_mod = _stub_module("mcp_instagram.session_manager")
        sm_mod.InstagramSessionManager = MagicMock
        sys.modules["mcp_instagram.session_manager"] = sm_mod


_inject_stubs()

import os
_INSTA_SRC = os.path.join(os.path.dirname(__file__), "..")
if _INSTA_SRC not in sys.path:
    sys.path.insert(0, _INSTA_SRC)

# Clear placeholder stubs for mcp_instagram so the real package can be loaded.
for _key in list(sys.modules.keys()):
    if _key == "mcp_instagram" or _key.startswith("mcp_instagram."):
        del sys.modules[_key]

# Restore the session_manager stub (instagrapi not installed in test env).
_sm_mod = _stub_module("mcp_instagram.session_manager")
_sm_mod.InstagramSessionManager = MagicMock
sys.modules["mcp_instagram.session_manager"] = _sm_mod

import mcp_instagram.server as srv  # noqa: E402


def _set_sessions(d: dict) -> None:
    """Replace _sessions in-place for test isolation."""
    srv._sessions.clear()
    srv._sessions.update(d)


def _mock_session() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# _resolve_username
# ---------------------------------------------------------------------------

def test_resolve_explicit_existing_username():
    """Explicitly-named username with an active session is returned as-is."""
    _set_sessions({"alice": _mock_session(), "bob": _mock_session()})
    assert srv._resolve_username("alice") == "alice"


def test_resolve_explicit_unknown_username_returns_none():
    """Explicitly-named username NOT in _sessions → None."""
    _set_sessions({"alice": _mock_session()})
    assert srv._resolve_username("charlie") is None


def test_resolve_empty_string_single_session_fallback():
    """Empty string with one session → returns that username."""
    _set_sessions({"alice": _mock_session()})
    assert srv._resolve_username("") == "alice"


def test_resolve_none_single_session_fallback():
    """None with one session → returns that username."""
    _set_sessions({"alice": _mock_session()})
    assert srv._resolve_username(None) == "alice"


def test_resolve_empty_string_zero_sessions_returns_none():
    """Empty string with no sessions → None."""
    _set_sessions({})
    assert srv._resolve_username("") is None


def test_resolve_none_zero_sessions_returns_none():
    """None with no sessions → None."""
    _set_sessions({})
    assert srv._resolve_username(None) is None


def test_resolve_empty_string_multiple_sessions_returns_none():
    """Empty string with multiple sessions → None (ambiguous)."""
    _set_sessions({"alice": _mock_session(), "bob": _mock_session()})
    assert srv._resolve_username("") is None


def test_resolve_none_multiple_sessions_returns_none():
    """None with multiple sessions → None."""
    _set_sessions({"alice": _mock_session(), "bob": _mock_session()})
    assert srv._resolve_username(None) is None


# ---------------------------------------------------------------------------
# _username_error messages
# ---------------------------------------------------------------------------

def test_username_error_no_sessions():
    """No sessions → tells user to run instagram_setup."""
    _set_sessions({})
    texts = srv._username_error(None)
    assert len(texts) == 1
    msg = texts[0].text
    assert "instagram_setup" in msg.lower() or "no instagram" in msg.lower()


def test_username_error_multiple_sessions():
    """Multiple sessions, no username → lists available accounts."""
    _set_sessions({"alice": _mock_session(), "bob": _mock_session()})
    texts = srv._username_error(None)
    msg = texts[0].text
    assert "alice" in msg
    assert "bob" in msg


def test_username_error_explicit_unknown():
    """Explicit unknown username → clear error naming that username."""
    _set_sessions({"alice": _mock_session()})
    texts = srv._username_error("charlie")
    msg = texts[0].text
    assert "charlie" in msg
    assert "instagram_setup" in msg.lower() or "no session" in msg.lower()


# ---------------------------------------------------------------------------
# Integration: handler uses resolver — empty username="" returns clear error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_read_dms_empty_username_no_session():
    """instagram_read_dms with username='' and no sessions returns a clear error."""
    _set_sessions({})
    result = await srv._handle_read_dms({"username": ""})
    assert len(result) == 1
    msg = result[0].text
    assert "instagram_setup" in msg.lower() or "no instagram" in msg.lower()


@pytest.mark.asyncio
async def test_handle_send_dm_empty_username_no_session():
    """instagram_send_dm with username='' and no sessions returns a clear error."""
    _set_sessions({})
    result = await srv._handle_send_dm({
        "username": "",
        "to_username": "target",
        "message": "hello",
    })
    assert len(result) == 1
    msg = result[0].text
    assert "instagram_setup" in msg.lower() or "no instagram" in msg.lower()


@pytest.mark.asyncio
async def test_handle_read_dms_empty_username_single_session_delegates():
    """instagram_read_dms with username='' and ONE session uses that session."""
    mgr = _mock_session()
    mgr.get_pending_dms = MagicMock(return_value=[])
    _set_sessions({"alice": mgr})

    result = await srv._handle_read_dms({"username": ""})
    # Should have reached the DM fetch path — result is JSON, not an error string
    assert len(result) == 1
    msg = result[0].text
    assert "instagram_setup" not in msg.lower(), (
        f"Should not error with single session; got: {msg!r}"
    )
    # get_pending_dms must have been called (resolver selected alice)
    mgr.get_pending_dms.assert_called_once()
