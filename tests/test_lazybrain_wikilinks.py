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


def test_rewrite_wikilink_target_basic_and_case_insensitive() -> None:
    md = "See [[Redis]] docs. Also [[REDIS]] and [[redis]] are same page."
    new_md, count = wikilinks.rewrite_wikilink_target(md, "Redis", "Valkey")
    assert count == 3
    assert "[[Valkey]]" in new_md
    assert "[[Redis]]" not in new_md
    assert "[[REDIS]]" not in new_md


def test_rewrite_wikilink_target_preserves_other_links() -> None:
    md = "Compare [[Redis]] with [[Memcached]]."
    new_md, count = wikilinks.rewrite_wikilink_target(md, "Redis", "Valkey")
    assert count == 1
    assert "[[Memcached]]" in new_md
    assert "[[Valkey]]" in new_md


def test_rewrite_wikilink_target_skips_fenced_code() -> None:
    md = "```\n[[Redis]] is a sample\n```\nBut [[Redis]] here is real."
    new_md, count = wikilinks.rewrite_wikilink_target(md, "Redis", "Valkey")
    assert count == 1
    # Fenced occurrence must stay untouched
    assert "[[Redis]] is a sample" in new_md
    # Non-fence occurrence rewritten
    assert "[[Valkey]] here is real." in new_md


def test_rewrite_wikilink_target_skips_inline_code() -> None:
    md = "Quote `[[Redis]]` but rewrite [[Redis]] outside."
    new_md, count = wikilinks.rewrite_wikilink_target(md, "Redis", "Valkey")
    assert count == 1
    assert "`[[Redis]]`" in new_md
    assert "[[Valkey]] outside." in new_md


def test_rewrite_wikilink_target_whitespace_normalization() -> None:
    md = "Pages: [[Lazy   Brain]] and [[ lazy brain ]]"
    new_md, count = wikilinks.rewrite_wikilink_target(md, "Lazy Brain", "Second Brain")
    assert count == 2
    assert "[[Second Brain]]" in new_md


def test_rewrite_wikilink_target_no_match_is_noop() -> None:
    md = "No references to that page."
    new_md, count = wikilinks.rewrite_wikilink_target(md, "Redis", "Valkey")
    assert count == 0
    assert new_md == md


def test_rewrite_wikilink_target_empty_inputs_are_safe() -> None:
    assert wikilinks.rewrite_wikilink_target("", "x", "y") == ("", 0)
    # Empty/whitespace old target short-circuits — nothing to search for
    assert wikilinks.rewrite_wikilink_target("text [[x]]", "", "y") == ("text [[x]]", 0)
    assert wikilinks.rewrite_wikilink_target("text [[x]]", "   ", "y") == ("text [[x]]", 0)
