"""Wikilinks + tag parser tests — the foundation of the backlink index.

If the parser misses links or mis-detects them, the graph silently
loses edges and backlinks panels go empty.
"""
from __future__ import annotations

from lazyclaw.lazybrain import wikilinks


def test_extracts_wikilinks_and_dedupes() -> None:
    text = "Reading [[Redis]] and [[Redis]] again, also [[Memcached]]."
    links = wikilinks.extract_wikilinks(text)
    assert links == ["redis", "memcached"], "case-fold + dedupe expected"


def test_case_fold_normalization() -> None:
    text = "Pages: [[Redis]], [[REDIS]], [[redis]]"
    assert wikilinks.extract_wikilinks(text) == ["redis"]


def test_strips_fenced_code() -> None:
    text = "```\n[[not-a-link]]\n```\n[[real-link]]"
    assert wikilinks.extract_wikilinks(text) == ["real-link"]


def test_strips_inline_code() -> None:
    text = "The tag `[[skip]]` is quoted, but [[take]] is real."
    assert wikilinks.extract_wikilinks(text) == ["take"]


def test_extract_tags_with_hierarchy() -> None:
    text = "Filed under #cache and #db/redis. Also #TIL."
    tags = wikilinks.extract_tags(text)
    assert tags == ["cache", "db/redis", "til"]


def test_ignores_hash_in_url() -> None:
    text = "See https://example.com/#fragment and #real-tag"
    assert "fragment" not in wikilinks.extract_tags(text)
    assert "real-tag" in wikilinks.extract_tags(text)


def test_parse_returns_pair() -> None:
    text = "[[Alpha]] tagged #beta"
    links, tags = wikilinks.parse(text)
    assert links == ["alpha"]
    assert tags == ["beta"]


def test_normalize_collapses_whitespace() -> None:
    assert wikilinks.normalize_page("  Lazy   Brain  ") == "lazy brain"


def test_empty_input_returns_empty_lists() -> None:
    assert wikilinks.extract_wikilinks("") == []
    assert wikilinks.extract_tags("") == []


def test_wikilinks_ignore_overlong_targets() -> None:
    huge = "[[" + "x" * 300 + "]]"
    assert wikilinks.extract_wikilinks(huge) == []
