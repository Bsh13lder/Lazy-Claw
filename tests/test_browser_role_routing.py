"""Tests for two-lane browser routing.

A turn's browser tool calls must reach the VISIBLE backend (foreground) or the
BACKGROUND backend (watcher/cron/reminder) depending on the active lane, and
the visible backend must never pick a tab owned by a background lane.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lazyclaw.browser import owned_tabs
from lazyclaw.runtime.browser_turn_lock import (
    BACKGROUND_ROLE,
    VISIBLE_ROLE,
    browser_turn_scope,
)
from lazyclaw.skills.builtin.browser_actions import backends


@pytest.fixture(autouse=True)
def _clean():
    owned_tabs._registry.clear()
    yield
    owned_tabs._registry.clear()


# ── _infer_browser_role (prefix → lane) ──────────────────────────────


@pytest.mark.parametrize(
    "message,expected",
    [
        ("[WATCHER:Watch: For new message] read the page", BACKGROUND_ROLE),
        ("[JOB:Task Guardian] check priorities", BACKGROUND_ROLE),
        ("[REMINDER] call the dentist", BACKGROUND_ROLE),
        ("Apply to Upwork job #1 from Vato's best-matches", VISIBLE_ROLE),
        ("check my whatsapp", VISIBLE_ROLE),
        ("", VISIBLE_ROLE),
    ],
)
def test_infer_browser_role(message, expected):
    from lazyclaw.runtime.agent import _infer_browser_role

    assert _infer_browser_role(message) == expected


# ── get_backend lane routing ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_backend_returns_injected_tab_context_unchanged():
    sentinel = object()
    assert await backends.get_backend("u1", tab_context=sentinel) is sentinel


@pytest.mark.asyncio
async def test_get_backend_visible_lane_routes_to_visible(monkeypatch):
    async def fake_visible(uid):
        return ("visible", uid)

    async def fake_bg(uid, *a, **k):
        return ("background", uid)

    monkeypatch.setattr(backends, "get_cdp_backend", fake_visible)
    monkeypatch.setattr(backends, "get_background_backend", fake_bg)

    async with browser_turn_scope(VISIBLE_ROLE):
        assert await backends.get_backend("u1") == ("visible", "u1")


@pytest.mark.asyncio
async def test_get_backend_background_lane_routes_to_background(monkeypatch):
    async def fake_visible(uid):
        return ("visible", uid)

    async def fake_bg(uid, *a, **k):
        return ("background", uid)

    monkeypatch.setattr(backends, "get_cdp_backend", fake_visible)
    monkeypatch.setattr(backends, "get_background_backend", fake_bg)

    async with browser_turn_scope(BACKGROUND_ROLE):
        assert await backends.get_backend("u1") == ("background", "u1")


@pytest.mark.asyncio
async def test_get_backend_outside_scope_defaults_to_visible(monkeypatch):
    async def fake_visible(uid):
        return ("visible", uid)

    async def fake_bg(uid, *a, **k):  # pragma: no cover - must not be called
        raise AssertionError("background backend used outside a background lane")

    monkeypatch.setattr(backends, "get_cdp_backend", fake_visible)
    monkeypatch.setattr(backends, "get_background_backend", fake_bg)

    assert await backends.get_backend("u1") == ("visible", "u1")


# ── visible backend excludes owned (background) tabs from MRU ─────────


def test_pick_preferred_tab_excludes_owned():
    from lazyclaw.browser.cdp_backend import CDPBackend

    b = CDPBackend(port=9222, user_id="u1")
    tabs = [SimpleNamespace(id="OWNED_BG"), SimpleNamespace(id="USER_VISIBLE")]

    # No owned tabs yet → plain MRU (first).
    assert b._pick_preferred_tab(tabs).id == "OWNED_BG"

    # Background lane owns the MRU tab → visible backend skips it.
    owned_tabs.set_owned("u1", "background", "OWNED_BG")
    assert b._pick_preferred_tab(tabs).id == "USER_VISIBLE"


def test_pick_preferred_tab_falls_back_when_all_owned():
    from lazyclaw.browser.cdp_backend import CDPBackend

    b = CDPBackend(port=9222, user_id="u1")
    tabs = [SimpleNamespace(id="A"), SimpleNamespace(id="B")]
    owned_tabs.set_owned("u1", "background", "A")
    owned_tabs.set_owned("u1", "watch:j", "B")
    # Every open tab is owned → correctness over isolation: still return MRU.
    assert b._pick_preferred_tab(tabs).id == "A"


def test_pick_preferred_tab_no_user_id_is_plain_mru():
    from lazyclaw.browser.cdp_backend import CDPBackend

    b = CDPBackend(port=9222, user_id=None)
    tabs = [SimpleNamespace(id="X"), SimpleNamespace(id="Y")]
    owned_tabs.set_owned("u1", "background", "X")  # different user — irrelevant
    assert b._pick_preferred_tab(tabs).id == "X"
