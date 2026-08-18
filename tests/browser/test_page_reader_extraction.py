"""run_extractor: listing pages, page mutation, and low-content fallback.

Incident 2026-08-18 (himap blog STEP 1): the public blog LISTING renders each
post card as an ``<article>``, so auto-detect chose the article extractor,
which (a) read only the FIRST card (~537 chars of husk while
document.body.innerText held 4187 chars with every post), and (b)
destructively ``remove()``d script/nav/aside nodes from the LIVE page —
mutating the user's real Brave tab on every read and confusing the SPA. The
specialist re-read the same 537 chars for 30+ iterations and the task died.

Pinned contracts:
1. A page with MULTIPLE <article> elements is a listing → generic extractor.
2. JS_ARTICLE must strip nodes on a CLONE, never the live DOM.
3. A suspiciously tiny extraction (< _LOW_CONTENT_FLOOR chars) on a page
   whose body text is far larger falls back to innerText.
"""

from __future__ import annotations

import asyncio

from lazyclaw.browser.page_reader import (
    _LOW_CONTENT_FLOOR,
    JS_ARTICLE,
    run_extractor,
)


class FakeBackend:
    """Scripted backend: routes evaluate() calls by recognizable JS snippets."""

    def __init__(
        self,
        *,
        n_articles: int = 0,
        extractor_result=None,
        body_text: str = "",
        url: str = "https://himap.co/blog",
    ):
        self._n_articles = n_articles
        self._extractor_result = extractor_result
        self._body_text = body_text
        self._url = url
        self.evaluated: list[str] = []

    async def current_url(self) -> str:
        return self._url

    async def title(self) -> str:
        return "Cannabis Blog | HiMap"

    async def evaluate(self, js: str):
        self.evaluated.append(js)
        if "querySelectorAll('article').length" in js:
            return self._n_articles
        if "!!document.querySelector('article')" in js:
            return self._n_articles > 0
        if "innerText" in js and ".length" in js:
            return len(self._body_text)
        if "innerText" in js:
            return self._body_text[:5000]
        # Any extractor body (JS_ARTICLE / JS_GENERIC / ...)
        return self._extractor_result


def _run(coro):
    return asyncio.run(coro)


class TestListingDetection:
    def test_multiple_articles_is_a_listing_not_an_article(self):
        # 12 post cards → must NOT use the single-article extractor.
        backend = FakeBackend(
            n_articles=12,
            extractor_result={
                "title": "Cannabis Blog", "url": "https://himap.co/blog",
                "text": "x" * 1000, "type": "generic",
            },
        )
        result = _run(run_extractor(backend))
        assert result["type"] == "generic"

    def test_single_article_page_uses_article_extractor(self):
        backend = FakeBackend(
            n_articles=1,
            extractor_result={
                "title": "How to Join", "url": "https://himap.co/blog/join",
                "text": "y" * 1000, "type": "article",
            },
        )
        result = _run(run_extractor(backend))
        assert result["type"] == "article"

    def test_zero_articles_is_generic(self):
        backend = FakeBackend(
            n_articles=0,
            extractor_result={
                "title": "t", "url": "u", "text": "z" * 1000, "type": "generic",
            },
        )
        result = _run(run_extractor(backend))
        assert result["type"] == "generic"


class TestNoLiveDomMutation:
    def test_js_article_strips_a_clone_not_the_page(self):
        assert "cloneNode(true)" in JS_ARTICLE


class TestLowContentFallback:
    def test_tiny_extraction_with_rich_body_falls_back_to_innertext(self):
        # The himap shape: extractor returns a 537-char husk while the body
        # holds the full 4187-char listing.
        backend = FakeBackend(
            n_articles=12,
            extractor_result={
                "title": "Cannabis Blog", "url": "https://himap.co/blog",
                "text": "h" * 537, "type": "generic",
            },
            body_text="B" * 4187,
        )
        result = _run(run_extractor(backend))
        assert result["type"] == "fallback_low_content"
        assert result["text"] == "B" * 4187

    def test_tiny_extraction_on_genuinely_tiny_page_stays(self):
        # A page that really only has 100 chars must not "fall back" to the
        # same 100 chars under a misleading type.
        backend = FakeBackend(
            n_articles=0,
            extractor_result={
                "title": "t", "url": "u", "text": "s" * 100, "type": "generic",
            },
            body_text="s" * 110,
        )
        result = _run(run_extractor(backend))
        assert result["type"] == "generic"

    def test_rich_extraction_never_falls_back(self):
        backend = FakeBackend(
            n_articles=1,
            extractor_result={
                "title": "t", "url": "u",
                "text": "r" * (_LOW_CONTENT_FLOOR + 500), "type": "article",
            },
            body_text="B" * 20000,
        )
        result = _run(run_extractor(backend))
        assert result["type"] == "article"
