"""Tests for the Cloudflare host guard in cdp_backend.

When the user opted into host-browser mode (``prefer_host=True``) but the
host Brave probe fails, ``_ensure_connected`` normally falls through to a
fresh container Chromium. For Cloudflare-protected hosts (upwork.com /
linkedin.com / user ``live_hosts``) that container browser fails CF's
fingerprint SILENTLY — empty results, no error. The guard refuses to
drive the wrong-identity browser and raises a clear "Cannot reach Brave"
RuntimeError instead.

Covers:
  - ``_target_needs_host_browser`` reuses the canonical CF host set
  - ``_ensure_connected`` RAISES for a CF target when host is unreachable
  - ``_ensure_connected`` does NOT raise (falls through) for a non-CF
    target — behavior for ordinary hosts is unchanged
  - No target_url (generic connect, e.g. current_url) never trips the guard

Everything is mocked — no real browser, no DB.
"""

from __future__ import annotations

import pytest

from lazyclaw.browser import cdp_backend
from lazyclaw.browser.cdp_backend import CDPBackend, _target_needs_host_browser


# ── _target_needs_host_browser (host classification) ─────────────────


@pytest.mark.asyncio
async def test_target_needs_host_browser_builtin_cf_hosts():
    """upwork.com / linkedin.com are CF-sensitive without any user extras."""
    assert await _target_needs_host_browser(
        "https://www.upwork.com/ab/messages", None,
    )
    assert await _target_needs_host_browser(
        "https://linkedin.com/feed", None,
    )
    assert await _target_needs_host_browser(
        "https://api.upwork.com/graphql", None,
    )


@pytest.mark.asyncio
async def test_target_needs_host_browser_ordinary_host_false():
    assert not await _target_needs_host_browser("https://example.com", None)
    assert not await _target_needs_host_browser("https://google.com/search", None)


@pytest.mark.asyncio
async def test_target_needs_host_browser_empty_or_bad_url():
    assert not await _target_needs_host_browser(None, None)
    assert not await _target_needs_host_browser("", None)
    assert not await _target_needs_host_browser("not-a-url", None)


@pytest.mark.asyncio
async def test_target_needs_host_browser_lookalike_not_cf():
    """Subdomain-anchor protects against lookalike domains (audit shape)."""
    assert not await _target_needs_host_browser("https://fakeupwork.com", None)
    assert not await _target_needs_host_browser(
        "https://upwork.com.evil.com", None,
    )


# ── _ensure_connected guard behavior ─────────────────────────────────


def _wire_unreachable_host(backend: CDPBackend, monkeypatch) -> None:
    """Make the host browser look opted-in but unreachable: every CDP
    endpoint probe returns a LOCAL (container) source. Also short-circuit
    the retry sleeps so the test is fast."""

    async def _resolve():
        return True, "host-token"  # prefer_host=True, has token

    async def _find(prefer_host, host_token):
        return "ws://127.0.0.1:9222/devtools/browser/x", "local"

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(backend, "_resolve_host_preference", _resolve)
    monkeypatch.setattr(backend, "_find_cdp_endpoint", _find)
    monkeypatch.setattr(cdp_backend.asyncio, "sleep", _no_sleep)


@pytest.mark.asyncio
async def test_ensure_connected_raises_for_cf_target(monkeypatch, tmp_path):
    """CF target + unreachable host browser → RuntimeError, NOT a silent
    fall-through to the container browser."""
    backend = CDPBackend(port=9222, user_id=None)
    _wire_unreachable_host(backend, monkeypatch)

    # Repair-flag write goes through load_config().database_dir — point it
    # at a temp dir so the existing self-heal block doesn't explode.
    class _Cfg:
        database_dir = tmp_path

    monkeypatch.setattr(
        "lazyclaw.config.load_config", lambda: _Cfg(), raising=False,
    )

    with pytest.raises(RuntimeError, match="Cannot reach Brave"):
        await backend._ensure_connected(
            target_url="https://www.upwork.com/ab/messages",
        )


@pytest.mark.asyncio
async def test_ensure_connected_no_raise_for_ordinary_host(monkeypatch, tmp_path):
    """Non-CF target + unreachable host → existing fallback path runs
    (we stop it before a real connect, but assert it did NOT raise the
    CF guard error)."""
    backend = CDPBackend(port=9222, user_id=None)
    _wire_unreachable_host(backend, monkeypatch)

    class _Cfg:
        database_dir = tmp_path

    monkeypatch.setattr(
        "lazyclaw.config.load_config", lambda: _Cfg(), raising=False,
    )

    # The non-CF path proceeds to the source=="local" branch which calls
    # list_chrome_tabs. Stub it to raise a DISTINCT sentinel so we can
    # prove the CF guard did NOT fire (no "Cannot reach Brave").
    async def _boom(*args, **kwargs):
        raise RuntimeError("__reached_local_branch__")

    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", _boom)

    with pytest.raises(RuntimeError) as exc:
        await backend._ensure_connected(target_url="https://example.com")
    assert "__reached_local_branch__" in str(exc.value)
    assert "Cannot reach Brave" not in str(exc.value)


@pytest.mark.asyncio
async def test_ensure_connected_no_target_skips_guard(monkeypatch, tmp_path):
    """A generic connect (no target_url — e.g. current_url) never trips
    the CF guard even when the host browser is unreachable."""
    backend = CDPBackend(port=9222, user_id=None)
    _wire_unreachable_host(backend, monkeypatch)

    class _Cfg:
        database_dir = tmp_path

    monkeypatch.setattr(
        "lazyclaw.config.load_config", lambda: _Cfg(), raising=False,
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("__reached_local_branch__")

    monkeypatch.setattr(cdp_backend, "list_chrome_tabs", _boom)

    with pytest.raises(RuntimeError) as exc:
        await backend._ensure_connected()  # no target_url
    assert "Cannot reach Brave" not in str(exc.value)
