"""Unit tests for apply_reddit_dm.

Pure-function tests on the username parser, compose URL builder, and
post-URL → author resolution. The browser/checkpoint paths are
exercised by integration in chat — not unit-testable without a Brave.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from lazyclaw.skills.builtin.survival.reddit_apply_skill import (
    _build_compose_url,
    _clean_username,
    _fallback_draft,
    _fetch_post_author,
    _post_id_from_url,
)
from lazyclaw.survival.profile import SkillsProfile


# ---- _clean_username ------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("blckit", "blckit"),
    ("/u/blckit", "blckit"),
    ("u/blckit", "blckit"),
    ("/U/BlckIt", "BlckIt"),
    ("  /u/blckit  ", "blckit"),
    ("user_with_underscore", "user_with_underscore"),
    ("user-with-hyphen", "user-with-hyphen"),
    ("123user", "123user"),
])
def test_clean_username_valid(raw, expected):
    assert _clean_username(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "u/with spaces",          # spaces invalid
    "u/with$symbol",           # $ invalid
    "u/" + "a" * 21,           # too long (>20 chars)
    "u/中文",                   # non-ascii
    "/u/",                     # empty after strip
])
def test_clean_username_rejects_invalid(raw):
    assert _clean_username(raw) == ""


# ---- _build_compose_url ---------------------------------------------------

def test_compose_url_basic():
    url = _build_compose_url("blckit", "Hello", "Hi there")
    assert url == (
        "https://www.reddit.com/message/compose/"
        "?to=blckit&subject=Hello&message=Hi+there"
    )


def test_compose_url_encodes_special_chars():
    url = _build_compose_url("user", "re: $50 gig?", "Line one\nLine two")
    # Check % encoding is applied — no raw $, ?, \n leaked into the URL
    assert "$" not in url.replace("?to=", "?to=")  # the only legal ? is the query separator
    assert "\n" not in url
    # Spaces and special chars in subject should be encoded
    assert "subject=re%3A+%2450+gig%3F" in url


def test_compose_url_handles_empty_message():
    url = _build_compose_url("user", "subj", "")
    assert "message=" in url
    assert url.endswith("message=")


# ---- _post_id_from_url ----------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.reddit.com/r/forhire/comments/1abcdef/title_slug/", "1abcdef"),
    ("https://reddit.com/r/forhire/comments/1abcdef/", "1abcdef"),
    ("https://old.reddit.com/r/forhire/comments/1abcdef/title/", "1abcdef"),
    ("https://new.reddit.com/r/forhire/comments/abc12/", "abc12"),
    ("http://reddit.com/r/forhire/comments/xyz789/", "xyz789"),
])
def test_post_id_from_url_extracts_id(url, expected):
    assert _post_id_from_url(url) == expected


@pytest.mark.parametrize("url", [
    "",
    "not a url",
    "https://example.com/foo",
    "https://reddit.com/user/blckit/",   # user URL, not post URL
    "https://reddit.com/r/forhire/",     # subreddit, not specific post
])
def test_post_id_from_url_rejects_non_post_urls(url):
    assert _post_id_from_url(url) is None


# ---- _fetch_post_author (mocked HTTP) -------------------------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Minimal AsyncClient stub for the get-only path used by the skill."""

    def __init__(self, *args, payload=None, status_code=200, **kwargs):
        self._payload = payload
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return _FakeResponse(self._payload, self._status_code)


def _make_post_payload(author="blckit", title="Test post"):
    """Mirror Reddit's [[listing, listing]] response shape for a single post."""
    return [
        {"data": {"children": [
            {"data": {"author": author, "title": title}},
        ]}},
        {"data": {"children": []}},  # comments listing — irrelevant
    ]


def test_fetch_post_author_happy_path():
    payload = _make_post_payload(author="someone", title="[Hiring] Python scraper")

    def _factory(*args, **kwargs):
        return _FakeAsyncClient(payload=payload)

    with patch(
        "lazyclaw.skills.builtin.survival.reddit_apply_skill.httpx.AsyncClient",
        _factory,
    ):
        author, title = asyncio.run(_fetch_post_author(
            "https://www.reddit.com/r/forhire/comments/1abc/title_slug/",
        ))
    assert author == "someone"
    assert title == "[Hiring] Python scraper"


def test_fetch_post_author_strips_query_string():
    """`.json` should be appended after stripping any ?utm=foo query string."""
    seen_url = {}

    class _CapturingClient(_FakeAsyncClient):
        async def get(self, url):
            seen_url["url"] = url
            return _FakeResponse(_make_post_payload())

    with patch(
        "lazyclaw.skills.builtin.survival.reddit_apply_skill.httpx.AsyncClient",
        _CapturingClient,
    ):
        asyncio.run(_fetch_post_author(
            "https://www.reddit.com/r/forhire/comments/1abc/?utm_source=share",
        ))
    assert seen_url["url"].endswith(".json")
    assert "utm_source" not in seen_url["url"]


def test_fetch_post_author_rejects_non_reddit_url():
    author, title = asyncio.run(_fetch_post_author("https://example.com/foo"))
    assert author == ""
    assert title == ""


def test_fetch_post_author_handles_deleted_author():
    payload = _make_post_payload(author="[deleted]", title="x")

    def _factory(*args, **kwargs):
        return _FakeAsyncClient(payload=payload)

    with patch(
        "lazyclaw.skills.builtin.survival.reddit_apply_skill.httpx.AsyncClient",
        _factory,
    ):
        author, title = asyncio.run(_fetch_post_author(
            "https://www.reddit.com/r/forhire/comments/1abc/",
        ))
    assert author == ""
    assert title == "x"


def test_fetch_post_author_handles_http_error():
    def _factory(*args, **kwargs):
        return _FakeAsyncClient(payload=None, status_code=503)

    with patch(
        "lazyclaw.skills.builtin.survival.reddit_apply_skill.httpx.AsyncClient",
        _factory,
    ):
        author, title = asyncio.run(_fetch_post_author(
            "https://www.reddit.com/r/forhire/comments/1abc/",
        ))
    assert author == ""
    assert title == ""


def test_fetch_post_author_handles_missing_keys():
    """Reddit can return non-standard listings (rate-limit, etc) — must not crash."""
    def _factory(*args, **kwargs):
        return _FakeAsyncClient(payload={"error": "rate-limited"})

    with patch(
        "lazyclaw.skills.builtin.survival.reddit_apply_skill.httpx.AsyncClient",
        _factory,
    ):
        author, title = asyncio.run(_fetch_post_author(
            "https://www.reddit.com/r/forhire/comments/1abc/",
        ))
    assert author == ""
    assert title == ""


# ---- _fallback_draft ------------------------------------------------------

def test_fallback_draft_lazyclaw_branding_includes_marker():
    profile = SkillsProfile(
        skills=("python", "scraping"),
        title="Python developer",
        bio="AI-augmented dev.",
        branding_mode="lazyclaw",
    )
    draft = _fallback_draft(
        username="someone",
        post_title="Need a Python scraper",
        profile=profile,
        custom_note="$50 budget",
    )
    assert "u/someone" in draft
    assert "LazyClaw" in draft
    assert "$50 budget" in draft
    assert "Need a Python scraper"[:60] in draft


def test_fallback_draft_personal_branding_no_lazyclaw_mention():
    profile = SkillsProfile(
        skills=("python",),
        title="Python developer",
        bio="Senior backend engineer.",
        branding_mode="personal",
    )
    draft = _fallback_draft(
        username="someone",
        post_title="API integration",
        profile=profile,
        custom_note="",
    )
    assert "LazyClaw" not in draft
    assert "u/someone" in draft


def test_fallback_draft_handles_empty_profile():
    profile = SkillsProfile()  # all defaults / empty
    draft = _fallback_draft(
        username="someone",
        post_title="",
        profile=profile,
        custom_note="",
    )
    assert "u/someone" in draft
    # Must produce *something* — never raise on empty profile
    assert len(draft) > 50


def test_fallback_draft_strips_long_post_title():
    long_title = "x" * 200
    profile = SkillsProfile(skills=("python",), branding_mode="personal")
    draft = _fallback_draft(
        username="someone",
        post_title=long_title,
        profile=profile,
        custom_note="",
    )
    # Title gets clipped at 60
    assert "x" * 200 not in draft
