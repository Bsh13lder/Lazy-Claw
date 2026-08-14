"""The 2026 settings/profile page no longer renders the user's name
anywhere (verified via live DOM probe 2026-08-01 — the page is pure
settings nav), so ``get_my_profile`` must fall back to the find-work
sidebar profile card (`[data-test="freelancer-sidebar-profile"]`
→ `a.profile-title`), a page the flow already visits for JSS.

Without this fallback, ``sync_upwork_profile`` stores no display_name,
``upwork_last_conversation`` can't resolve ``me_name``, and the MCP
class-hint fallback mis-tags the user's own bubbles as the contact
(sender-confabulation class).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from upwork_mcp.tools import profile as profile_mod


class _FakeEl:
    def __init__(self, text: str) -> None:
        self._text = text

    async def text_content(self) -> str:
        return self._text


class _FakePage:
    """Returns elements per-selector; unknown selectors miss."""

    def __init__(self, by_selector: dict[str, str]) -> None:
        self._by_selector = by_selector

    async def query_selector(self, selector: str):
        for fragment, text in self._by_selector.items():
            if fragment in selector:
                return _FakeEl(text)
        return None

    async def query_selector_all(self, selector: str):
        return []


class _FakeBrowser:
    def __init__(self, pages_by_url: dict[str, _FakePage]) -> None:
        self._pages = pages_by_url

    async def ensure_logged_in(self) -> None:
        return None

    async def safe_goto(self, url: str) -> _FakePage:
        for fragment, page in self._pages.items():
            if fragment in url:
                return page
        return _FakePage({})


@pytest.fixture
def _quiet_connects(monkeypatch):
    monkeypatch.setattr(
        profile_mod, "get_connects_balance", AsyncMock(return_value={}),
    )


@pytest.mark.asyncio
async def test_name_falls_back_to_findwork_sidebar(monkeypatch, _quiet_connects):
    """Settings page has no name element → sidebar profile card wins."""
    settings_page = _FakePage({})  # 2026 layout: nothing name-shaped
    findwork_page = _FakePage({"freelancer-sidebar-profile": "Vato T."})
    monkeypatch.setattr(
        profile_mod, "get_browser",
        lambda: _FakeBrowser({
            "settings/profile": settings_page,
            "find-work": findwork_page,
        }),
    )

    result = await profile_mod.get_my_profile()

    assert result.get("name") == "Vato T."


@pytest.mark.asyncio
async def test_sidebar_nav_noise_is_not_a_name(monkeypatch, _quiet_connects):
    """A nav label leaking through the sidebar selector must be dropped."""
    findwork_page = _FakePage({"freelancer-sidebar-profile": "Settings"})
    monkeypatch.setattr(
        profile_mod, "get_browser",
        lambda: _FakeBrowser({
            "settings/profile": _FakePage({}),
            "find-work": findwork_page,
        }),
    )

    result = await profile_mod.get_my_profile()

    assert "name" not in result


@pytest.mark.asyncio
async def test_sidebar_missing_leaves_name_unset(monkeypatch, _quiet_connects):
    """Both sources miss → no name key (caller treats empty as skip)."""
    monkeypatch.setattr(
        profile_mod, "get_browser",
        lambda: _FakeBrowser({
            "settings/profile": _FakePage({}),
            "find-work": _FakePage({}),
        }),
    )

    result = await profile_mod.get_my_profile()

    assert "name" not in result


@pytest.mark.asyncio
async def test_settings_name_still_wins_when_present(monkeypatch, _quiet_connects):
    """If Upwork restores a structured name on settings, it takes priority."""
    settings_page = _FakePage({"profile-name": "Vato Tsereteli"})
    findwork_page = _FakePage({"freelancer-sidebar-profile": "Vato T."})
    monkeypatch.setattr(
        profile_mod, "get_browser",
        lambda: _FakeBrowser({
            "settings/profile": settings_page,
            "find-work": findwork_page,
        }),
    )

    result = await profile_mod.get_my_profile()

    assert result.get("name") == "Vato Tsereteli"
