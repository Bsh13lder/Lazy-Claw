"""Tests for structured "empty vs blocked" returns on the read paths.

Background (2026-06-10 audit): five read tools returned bare ``[]`` / ``{}``
on selector miss or page failure, with diagnostics only in stderr logs —
the calling brain could not tell "genuinely no results" from "Cloudflare
wall" from "session expired". 53 occurrences of the get_messages
"0 room elements" warning in one log file.

Contract under test: when extraction yields zero items, the tool returns

    {"status": "empty_or_blocked", "items": [], "page_url": ..., "page_title": ...,
     "diagnosis": "cloudflare_challenge" | "login_required" |
                  "selector_drift_or_truly_empty",
     "hint": <one actionable sentence>}

Also covers ``dropped_suspect_bubbles`` surfacing in
``get_conversation_messages`` (timestamp-filter transparency) and the
FastMCP structured-output compatibility of the widened server.py return
annotations (a dict where a list used to be returned must not crash the
serializer).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── fakes ───────────────────────────────────────────────────────────────


class FakePage:
    """Minimal Playwright-page stand-in: every selector misses."""

    def __init__(
        self,
        url: str = "https://www.upwork.com/nx/find-work/best-matches",
        title: str = "Find Work - Upwork",
        content: str = "<html><body>feed</body></html>",
        redirect_to: str | None = None,
    ):
        self.url = url
        self._title = title
        self._content = content
        self._redirect_to = redirect_to

    async def title(self):
        return self._title

    async def content(self):
        return self._content

    async def goto(self, url, wait_until=None):
        self.url = self._redirect_to if self._redirect_to else url

    async def reload(self, wait_until=None):
        return None

    async def query_selector(self, selector):
        return None

    async def query_selector_all(self, selector):
        return []

    async def wait_for_selector(self, selector, timeout=None):
        raise TimeoutError(f"no element matched: {selector}")

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def evaluate(self, js):
        return {"scrolled": True, "count": 0}

    def is_closed(self):
        return False


class RaisingPage(FakePage):
    """Page whose state accessors all raise (dead handle mid-diagnosis)."""

    async def title(self):
        raise RuntimeError("Target closed")

    async def content(self):
        raise RuntimeError("Target closed")


def _fake_browser(page: FakePage) -> MagicMock:
    browser = MagicMock()
    browser.ensure_logged_in = AsyncMock(return_value=True)
    browser.get_page = AsyncMock(return_value=page)
    browser.safe_goto = AsyncMock(return_value=page)
    return browser


_CF_PAGE = dict(
    url="https://www.upwork.com/ab/messages/rooms/?__cf_chl_tk=x",
    title="Just a moment...",
    content="<html><body>cf-browser-verification</body></html>",
)


def _assert_structured_empty(result, expected_diagnosis: str):
    assert isinstance(result, dict)
    assert result["status"] == "empty_or_blocked"
    assert result["items"] == []
    assert result["diagnosis"] == expected_diagnosis
    assert result["page_url"]
    assert result["page_title"]
    assert isinstance(result["hint"], str) and result["hint"]


# ─── diagnosis helper unit tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_diagnose_cloudflare_by_url():
    from upwork_mcp.tools.page_state import diagnose_page_state

    page = FakePage(url="https://challenges.cloudflare.com/turnstile/x")
    diagnosis, _, _ = await diagnose_page_state(page)
    assert diagnosis == "cloudflare_challenge"


@pytest.mark.asyncio
async def test_diagnose_cloudflare_by_title():
    from upwork_mcp.tools.page_state import diagnose_page_state

    page = FakePage(title="Just a moment...")
    diagnosis, _, _ = await diagnose_page_state(page)
    assert diagnosis == "cloudflare_challenge"


@pytest.mark.asyncio
async def test_diagnose_cloudflare_by_body_marker():
    from upwork_mcp.tools.page_state import diagnose_page_state

    page = FakePage(content="<div id='cf-browser-verification'></div>")
    diagnosis, _, _ = await diagnose_page_state(page)
    assert diagnosis == "cloudflare_challenge"


@pytest.mark.asyncio
async def test_diagnose_login_redirect():
    from upwork_mcp.tools.page_state import diagnose_page_state

    page = FakePage(url="https://www.upwork.com/ab/account-security/login")
    diagnosis, _, _ = await diagnose_page_state(page)
    assert diagnosis == "login_required"


@pytest.mark.asyncio
async def test_diagnose_plain_login_path():
    from upwork_mcp.tools.page_state import diagnose_page_state

    page = FakePage(url="https://www.upwork.com/login?redir=%2Fnx")
    diagnosis, _, _ = await diagnose_page_state(page)
    assert diagnosis == "login_required"


@pytest.mark.asyncio
async def test_diagnose_defaults_to_selector_drift():
    from upwork_mcp.tools.page_state import diagnose_page_state

    page = FakePage()  # normal logged-in feed page
    diagnosis, _, _ = await diagnose_page_state(page)
    assert diagnosis == "selector_drift_or_truly_empty"


@pytest.mark.asyncio
async def test_diagnose_tolerates_none_page():
    from upwork_mcp.tools.page_state import empty_or_blocked_result

    result = await empty_or_blocked_result(None)
    _assert_structured_empty(result, "selector_drift_or_truly_empty")


@pytest.mark.asyncio
async def test_diagnose_never_raises_on_dead_page():
    from upwork_mcp.tools.page_state import empty_or_blocked_result

    result = await empty_or_blocked_result(RaisingPage())
    assert result["status"] == "empty_or_blocked"
    assert result["diagnosis"] in (
        "cloudflare_challenge",
        "login_required",
        "selector_drift_or_truly_empty",
    )


@pytest.mark.asyncio
async def test_hints_are_actionable():
    from upwork_mcp.tools.page_state import empty_or_blocked_result

    cf = await empty_or_blocked_result(FakePage(**_CF_PAGE))
    assert "Brave" in cf["hint"]  # solve it in Brave

    drift = await empty_or_blocked_result(FakePage())
    assert "browser tool" in drift["hint"]  # verify via own browser tool

    login = await empty_or_blocked_result(
        FakePage(url="https://www.upwork.com/ab/account-security/login")
    )
    assert "sign in" in login["hint"].lower()


# ─── site 1: search_jobs ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_jobs_zero_tiles_returns_structured_dict():
    from upwork_mcp.tools.jobs import JobSearchParams, search_jobs

    page = FakePage()
    with patch(
        "upwork_mcp.tools.jobs.get_browser", return_value=_fake_browser(page)
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await search_jobs(JobSearchParams(query="python"))

    _assert_structured_empty(result, "selector_drift_or_truly_empty")


@pytest.mark.asyncio
async def test_search_jobs_cloudflare_diagnosis():
    from upwork_mcp.tools.jobs import JobSearchParams, search_jobs

    page = FakePage(**_CF_PAGE)
    with patch(
        "upwork_mcp.tools.jobs.get_browser", return_value=_fake_browser(page)
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await search_jobs(JobSearchParams(query="python"))

    _assert_structured_empty(result, "cloudflare_challenge")


# ─── site 2: get_offers ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_offers_all_urls_failed_returns_structured_dict():
    from upwork_mcp.tools.offers import OffersParams, get_offers

    page = FakePage(url="https://www.upwork.com/nx/offers/")
    with patch(
        "upwork_mcp.tools.offers.get_browser", return_value=_fake_browser(page)
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_offers(OffersParams())

    _assert_structured_empty(result, "selector_drift_or_truly_empty")


@pytest.mark.asyncio
async def test_get_offers_every_goto_raised_still_structured():
    """When every candidate URL's safe_goto raises, there is no page
    handle to diagnose — the structured dict must still come back."""
    from upwork_mcp.tools.offers import OffersParams, get_offers

    browser = MagicMock()
    browser.ensure_logged_in = AsyncMock(return_value=True)
    browser.safe_goto = AsyncMock(side_effect=RuntimeError("Target closed"))
    with patch(
        "upwork_mcp.tools.offers.get_browser", return_value=browser
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_offers(OffersParams())

    _assert_structured_empty(result, "selector_drift_or_truly_empty")


# ─── site 3: get_contracts ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_contracts_selector_timeout_returns_structured_dict():
    from upwork_mcp.tools.contracts import ContractsParams, get_contracts

    page = FakePage(url="https://www.upwork.com/nx/wm/contracts?status=active")
    with patch(
        "upwork_mcp.tools.contracts.get_browser",
        return_value=_fake_browser(page),
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_contracts(ContractsParams())

    _assert_structured_empty(result, "selector_drift_or_truly_empty")


@pytest.mark.asyncio
async def test_get_contracts_login_redirect_diagnosis():
    from upwork_mcp.tools.contracts import ContractsParams, get_contracts

    # get_contracts navigates via browser.safe_goto (serialized on
    # _NAV_LOCK + Cloudflare-resilient), which the fake stubs out — so
    # express the post-redirect state as the page's url, the same way the
    # other safe_goto-based sites do (search_jobs, get_messages,
    # get_connects_balance). The old `redirect_to=` hook only fired
    # through the raw page.goto() this tool no longer calls.
    page = FakePage(url="https://www.upwork.com/ab/account-security/login")
    with patch(
        "upwork_mcp.tools.contracts.get_browser",
        return_value=_fake_browser(page),
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_contracts(ContractsParams())

    _assert_structured_empty(result, "login_required")


# ─── site 4: get_messages ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_messages_zero_rooms_returns_structured_dict():
    from upwork_mcp.tools.messages import MessagesParams, get_messages

    page = FakePage(
        url="https://www.upwork.com/ab/messages/rooms/",
        title="Messages | Upwork",
    )
    with patch(
        "upwork_mcp.tools.messages.get_browser",
        return_value=_fake_browser(page),
    ), patch(
        "upwork_mcp.tools.messages._load_seen_rooms", return_value=set()
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_messages(MessagesParams())

    _assert_structured_empty(result, "selector_drift_or_truly_empty")


@pytest.mark.asyncio
async def test_get_messages_cloudflare_diagnosis():
    from upwork_mcp.tools.messages import MessagesParams, get_messages

    page = FakePage(**_CF_PAGE)
    with patch(
        "upwork_mcp.tools.messages.get_browser",
        return_value=_fake_browser(page),
    ), patch(
        "upwork_mcp.tools.messages._load_seen_rooms", return_value=set()
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_messages(MessagesParams())

    _assert_structured_empty(result, "cloudflare_challenge")


# ─── site 5: get_connects_balance ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_connects_balance_all_selectors_miss_returns_structured():
    from upwork_mcp.tools.profile import get_connects_balance

    page = FakePage(url="https://www.upwork.com/nx/plans/connects/history")
    with patch(
        "upwork_mcp.tools.profile.get_browser",
        return_value=_fake_browser(page),
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_connects_balance()

    _assert_structured_empty(result, "selector_drift_or_truly_empty")


@pytest.mark.asyncio
async def test_get_connects_balance_login_diagnosis():
    from upwork_mcp.tools.profile import get_connects_balance

    page = FakePage(url="https://www.upwork.com/ab/account-security/login")
    with patch(
        "upwork_mcp.tools.profile.get_browser",
        return_value=_fake_browser(page),
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await get_connects_balance()

    _assert_structured_empty(result, "login_required")


# ─── C. timestamp-filter drop count surfacing ────────────────────────────


def _conversation_page() -> FakePage:
    page = FakePage(
        url="https://www.upwork.com/ab/messages/rooms/room_abc123",
        title="James Blue",
    )

    async def _qsa(selector):
        if "story-container" in selector:
            return [MagicMock()]
        return []

    page.query_selector_all = _qsa
    page.evaluate = AsyncMock(return_value={"scrolled": True, "count": 1})
    return page


def _bubble_handle():
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value="bubble-class")
    return el


@pytest.mark.asyncio
async def test_dropped_suspect_bubbles_surfaced_in_payload():
    from upwork_mcp.tools import messages as messages_mod

    msgs = [
        {"sender": "James Blue", "timestamp": "10:37 PM", "content": "hi", "is_mine": False},
        {"sender": "James Blue", "timestamp": "FUTURE 11:59 PM", "content": "ghost", "is_mine": False},
        {"sender": "James Blue", "timestamp": "10:39 PM", "content": "bye", "is_mine": False},
    ]
    handles = [_bubble_handle() for _ in msgs]

    with patch.object(
        messages_mod, "get_browser",
        return_value=_fake_browser(_conversation_page()),
    ), patch.object(
        messages_mod, "_gather_all_bubbles_iter",
        new=AsyncMock(return_value=(handles, 0)),
    ), patch.object(
        messages_mod, "_extract_message", new=AsyncMock(side_effect=msgs),
    ), patch.object(
        messages_mod, "_bubble_timestamp_in_future",
        new=MagicMock(side_effect=lambda ts: "FUTURE" in ts),
    ), patch("asyncio.sleep", new=AsyncMock()):
        conversation = await messages_mod.get_conversation_messages("room_abc123")

    assert len(conversation["messages"]) == 2
    assert conversation["dropped_suspect_bubbles"] == 1


@pytest.mark.asyncio
async def test_no_drops_means_no_dropped_key():
    from upwork_mcp.tools import messages as messages_mod

    msgs = [
        {"sender": "James Blue", "timestamp": "10:37 PM", "content": "hi", "is_mine": False},
        {"sender": "James Blue", "timestamp": "10:39 PM", "content": "bye", "is_mine": False},
    ]
    handles = [_bubble_handle() for _ in msgs]

    with patch.object(
        messages_mod, "get_browser",
        return_value=_fake_browser(_conversation_page()),
    ), patch.object(
        messages_mod, "_gather_all_bubbles_iter",
        new=AsyncMock(return_value=(handles, 0)),
    ), patch.object(
        messages_mod, "_extract_message", new=AsyncMock(side_effect=msgs),
    ), patch.object(
        messages_mod, "_bubble_timestamp_in_future",
        new=MagicMock(return_value=False),
    ), patch("asyncio.sleep", new=AsyncMock()):
        conversation = await messages_mod.get_conversation_messages("room_abc123")

    assert len(conversation["messages"]) == 2
    assert "dropped_suspect_bubbles" not in conversation


# ─── FastMCP serializer compatibility ────────────────────────────────────
#
# server.py wrappers were annotated ``-> list[dict]``; FastMCP 1.26
# derives a structured-output schema from that annotation and VALIDATES
# the result against it — a dict return would raise a pydantic
# ValidationError inside the serializer. The annotations must be widened
# to ``list[dict] | dict`` for every tool that can now return the
# structured empty dict.

_SAMPLE_EMPTY = {
    "status": "empty_or_blocked",
    "items": [],
    "page_url": "https://www.upwork.com/nx/find-work/best-matches",
    "page_title": "Find Work - Upwork",
    "diagnosis": "selector_drift_or_truly_empty",
    "hint": "Verify via your own browser tool.",
}


@pytest.mark.parametrize(
    "tool_name",
    [
        "upwork_search_jobs",
        "upwork_get_messages",
        "upwork_get_contracts",
        "upwork_get_offers",
    ],
)
def test_server_serializer_accepts_structured_dict(tool_name):
    from upwork_mcp import server

    tool = server.mcp._tool_manager.get_tool(tool_name)
    assert tool is not None
    # Must not raise — this is exactly what FastMCP does on tool return.
    tool.fn_metadata.convert_result(_SAMPLE_EMPTY)
    # And the legacy list shape must still serialize.
    tool.fn_metadata.convert_result([{"title": "some job"}])


def test_server_connects_balance_serializer_accepts_structured_dict():
    from upwork_mcp import server

    tool = server.mcp._tool_manager.get_tool("upwork_get_connects_balance")
    assert tool is not None
    tool.fn_metadata.convert_result(_SAMPLE_EMPTY)
    tool.fn_metadata.convert_result({"available": 28})
