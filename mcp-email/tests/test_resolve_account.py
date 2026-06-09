"""Unit tests for _resolve_account and _account_error in mcp_email.server.

These tests exercise only the pure-Python resolver logic; they mock out
``aiosmtplib`` and ``mcp`` so the module can be imported without the full
MCP stack being installed.
"""
import sys
import types
from unittest.mock import MagicMock, AsyncMock, patch
import pytest


# ---------------------------------------------------------------------------
# Bootstrap: provide lightweight stubs for heavy dependencies so the server
# module imports cleanly in the bare test environment.
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _inject_stubs() -> None:
    """Inject minimal stubs for aiosmtplib and mcp.* before importing server."""
    # aiosmtplib
    if "aiosmtplib" not in sys.modules:
        sys.modules["aiosmtplib"] = _stub_module("aiosmtplib", SMTP=MagicMock)

    # mcp tree
    for mod_name in ("mcp", "mcp.server", "mcp.server.stdio", "mcp.types"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _stub_module(mod_name)

    # Server instance needs decorator-compatible .list_tools() / .call_tool()
    # that return callables so @app.list_tools() / @app.call_tool() don't fail.
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
        mcp_types.TextContent = MagicMock
    if not hasattr(mcp_types, "Tool"):
        mcp_types.Tool = MagicMock

    mcp_stdio = sys.modules["mcp.server.stdio"]
    if not hasattr(mcp_stdio, "stdio_server"):
        mcp_stdio.stdio_server = MagicMock

    # mcp_email.providers
    if "mcp_email" not in sys.modules:
        sys.modules["mcp_email"] = _stub_module("mcp_email")
    if "mcp_email.providers" not in sys.modules:
        sys.modules["mcp_email.providers"] = _stub_module(
            "mcp_email.providers", detect_provider=MagicMock(return_value={})
        )


_inject_stubs()

# Add the real mcp-email source to sys.path BEFORE importing.
# Remove the stub placeholder for mcp_email (inserted during _inject_stubs
# to satisfy the providers sub-import) so the real package takes over.
import os
_EMAIL_SRC = os.path.join(os.path.dirname(__file__), "..")
if _EMAIL_SRC not in sys.path:
    sys.path.insert(0, _EMAIL_SRC)

# Clear any shallow stub that was set for "mcp_email" so the real package loads.
for _key in list(sys.modules.keys()):
    if _key == "mcp_email" or _key.startswith("mcp_email."):
        del sys.modules[_key]

# Re-inject only the providers stub (real providers.py in the package is fine,
# but if it imports something missing, keep the stub).
sys.modules["mcp_email.providers"] = _stub_module(
    "mcp_email.providers", detect_provider=MagicMock(return_value={})
)

import mcp_email.server as srv  # noqa: E402  (must come after stubs)


def _set_configs(d: dict) -> None:
    """Replace _configs in-place for test isolation."""
    srv._configs.clear()
    srv._configs.update(d)


# ---------------------------------------------------------------------------
# _resolve_account
# ---------------------------------------------------------------------------

def test_resolve_explicit_existing_account():
    """When a known addr is passed explicitly, it is returned as-is."""
    _set_configs({"alice@example.com": {}, "bob@example.com": {}})
    assert srv._resolve_account("alice@example.com") == "alice@example.com"


def test_resolve_explicit_unknown_account_returns_none():
    """When an explicitly-passed addr is NOT in _configs, return None."""
    _set_configs({"alice@example.com": {}})
    assert srv._resolve_account("unknown@example.com") is None


def test_resolve_empty_string_single_account_fallback():
    """Empty string with one account configured → returns that account."""
    _set_configs({"alice@example.com": {}})
    assert srv._resolve_account("") == "alice@example.com"


def test_resolve_none_single_account_fallback():
    """None with one account configured → returns that account."""
    _set_configs({"alice@example.com": {}})
    assert srv._resolve_account(None) == "alice@example.com"


def test_resolve_empty_string_zero_accounts_returns_none():
    """Empty string with no accounts → None (caller should report error)."""
    _set_configs({})
    assert srv._resolve_account("") is None


def test_resolve_none_zero_accounts_returns_none():
    """None with no accounts configured → None."""
    _set_configs({})
    assert srv._resolve_account(None) is None


def test_resolve_empty_string_multiple_accounts_returns_none():
    """Empty string with multiple accounts → None (ambiguous, caller must specify)."""
    _set_configs({"alice@example.com": {}, "bob@example.com": {}})
    assert srv._resolve_account("") is None


def test_resolve_none_multiple_accounts_returns_none():
    """None with multiple accounts → None."""
    _set_configs({"alice@example.com": {}, "bob@example.com": {}})
    assert srv._resolve_account(None) is None


# ---------------------------------------------------------------------------
# _account_error messages
# ---------------------------------------------------------------------------

def test_account_error_no_arg_no_configs():
    """No arg, no configs → tells user to run email_setup."""
    _set_configs({})
    msg = srv._account_error(None)
    assert "email_setup" in msg.lower() or "configured" in msg.lower()


def test_account_error_no_arg_multiple_configs():
    """No arg, multiple accounts → asks user to specify and lists accounts."""
    _set_configs({"alice@example.com": {}, "bob@example.com": {}})
    msg = srv._account_error(None)
    assert "alice@example.com" in msg
    assert "bob@example.com" in msg


def test_account_error_explicit_unknown():
    """Explicit unknown addr → tells user that specific account is not configured."""
    _set_configs({"alice@example.com": {}})
    msg = srv._account_error("unknown@example.com")
    assert "unknown@example.com" in msg
    assert "not configured" in msg.lower() or "email_setup" in msg.lower()


# ---------------------------------------------------------------------------
# Integration: handler uses resolver — empty email="" returns clear error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_send_empty_email_no_account_configured():
    """email_send with email='' and no configured accounts returns a clear error."""
    _set_configs({})
    result = await srv._handle_send({
        "email": "",
        "to": "recipient@example.com",
        "subject": "Hi",
        "body": "Test",
    })
    assert "configured" in result.lower() or "email_setup" in result.lower()
    # The error message must NOT look like a success
    assert result.lower() != "sent to recipient@example.com"


@pytest.mark.asyncio
async def test_handle_search_empty_email_no_account_configured():
    """email_search with email='' and no configured accounts returns a clear error."""
    _set_configs({})
    result = await srv._handle_search({"email": "", "query": "test"})
    assert "configured" in result.lower() or "email_setup" in result.lower()


@pytest.mark.asyncio
async def test_handle_send_empty_email_single_account_uses_it():
    """email_send with email='' and ONE configured account delegates to that account.

    We don't make a real SMTP connection — just assert that the handler
    proceeds past the resolver (hits the SMTP path, not the error return).
    """
    _set_configs({"alice@example.com": {
        "email": "alice@example.com",
        "password": "pw",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "imap_host": "imap.example.com",
        "imap_port": 993,
    }})

    smtp_mock = AsyncMock()
    smtp_mock.connect = AsyncMock()
    smtp_mock.login = AsyncMock()
    smtp_mock.send_message = AsyncMock()
    smtp_mock.quit = AsyncMock()

    # Patch aiosmtplib.SMTP so we don't need a real server
    with patch.object(sys.modules["aiosmtplib"], "SMTP", return_value=smtp_mock):
        result = await srv._handle_send({
            "email": "",
            "to": "recipient@example.com",
            "subject": "Hi",
            "body": "Test body",
        })

    # Should have reached the send path, not the resolver error path
    assert "configured" not in result.lower(), (
        f"Unexpected error when single account present: {result!r}"
    )
    assert "Sent to" in result
