"""Tests for AdaptiveSelector — the selectors that survive DOM redesigns.

Pinned contract:
  * Cold path stores the fingerprint and returns the element
  * Identical DOM next visit returns status="hit" via the saved CSS path
  * DOM with renamed class but same content → status="relocated", saved
    CSS path is updated to the new path so future calls fast-path
  * Major redesign with no similar element → status="broken" + warning
  * Tempfile DB per test; no global state leakage

No real network, no real fixtures from disk — all tests use inline HTML.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp-scraper"))

from mcp_scraper.adaptive_selector import (  # noqa: E402
    AdaptiveSelector,
    FindResult,
    _attr_jaccard,
    _fingerprint,
    _text_similarity,
)


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "selectors.db"


# ── Pure-function unit tests (no DB) ───────────────────────────────────

def test_fingerprint_stable_across_calls():
    a = _fingerprint("div", {"class": "x"}, "hello")
    b = _fingerprint("div", {"class": "x"}, "hello")
    assert a == b


def test_fingerprint_changes_when_text_changes():
    a = _fingerprint("div", {"class": "x"}, "hello")
    b = _fingerprint("div", {"class": "x"}, "world")
    assert a != b


def test_fingerprint_normalizes_whitespace():
    a = _fingerprint("div", {}, "hello world")
    b = _fingerprint("div", {}, "  hello   world  ")
    assert a == b


def test_attr_jaccard_identical_is_one():
    assert _attr_jaccard({"a": "1", "b": "2"}, {"a": "1", "b": "2"}) == 1.0


def test_attr_jaccard_disjoint_is_zero():
    assert _attr_jaccard({"a": "1"}, {"b": "2"}) == 0.0


def test_attr_jaccard_partial_overlap():
    score = _attr_jaccard({"a": "1", "b": "2"}, {"a": "1", "c": "3"})
    assert 0.0 < score < 1.0


def test_attr_jaccard_both_empty_is_one():
    assert _attr_jaccard({}, {}) == 1.0


def test_text_similarity_identical_is_one():
    assert _text_similarity("hello world", "hello world") == 1.0


def test_text_similarity_partial_overlap():
    s = _text_similarity("hello world foo", "hello world bar")
    assert 0.0 < s < 1.0


# ── Cold path: first visit ─────────────────────────────────────────────

def test_cold_visit_stores_fingerprint(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    html = '<html><body><div class="price">€42.00</div></body></html>'
    result = sel.find(
        html, domain="shop.com", selector_id="price", initial_css=".price",
    )
    assert result.status == "cold"
    assert result.text == "€42.00"
    assert sel.stats()["total"] == 1


def test_cold_visit_with_missing_initial_selector_returns_broken(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    html = "<html><body><div>nothing here</div></body></html>"
    result = sel.find(
        html, domain="shop.com", selector_id="price",
        initial_css=".does-not-exist",
    )
    assert result.status == "broken"
    assert result.element is None
    # Nothing stored — caller must fix the seed selector
    assert sel.stats()["total"] == 0


# ── Hit path: identical DOM next visit ─────────────────────────────────

def test_repeat_visit_with_identical_dom_returns_hit(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    html = '<html><body><div class="price">€42.00</div></body></html>'

    # First visit (cold)
    sel.find(html, domain="shop.com", selector_id="price", initial_css=".price")

    # Second visit (hit) — same HTML
    result = sel.find(
        html, domain="shop.com", selector_id="price", initial_css=".price",
    )
    assert result.status == "hit"
    assert result.text == "€42.00"
    assert result.score == 1.0


# ── Relocation: DOM redesign with renamed class ────────────────────────

def test_renamed_class_triggers_relocation(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    html_v1 = (
        '<html><body><div class="old-name product-price" data-id="42">'
        '€42.00</div></body></html>'
    )
    html_v2 = (
        '<html><body><div class="new-name product-price" data-id="42">'
        '€42.00</div></body></html>'
    )

    # First visit pins ".old-name"
    r1 = sel.find(
        html_v1, domain="shop.com", selector_id="price",
        initial_css=".old-name",
    )
    assert r1.status == "cold"

    # Second visit — class renamed; saved selector misses but Jaccard
    # on (data-id, product-price) + identical text pulls us back
    r2 = sel.find(
        html_v2, domain="shop.com", selector_id="price",
        initial_css=".old-name",
    )
    assert r2.status == "relocated"
    assert r2.text == "€42.00"
    assert r2.score >= 0.7

    # Third visit — saved CSS path was updated; should now be a hit on
    # the SAME html_v2 (since v2 is the new "current" reality)
    r3 = sel.find(
        html_v2, domain="shop.com", selector_id="price",
        initial_css=".old-name",
    )
    assert r3.status == "hit"


def test_relocation_increments_relocate_count(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    html_v1 = (
        '<html><body><div class="old" data-stable="x">42</div></body></html>'
    )
    html_v2 = (
        '<html><body><div class="renamed" data-stable="x">42</div></body></html>'
    )

    sel.find(html_v1, domain="x.com", selector_id="thing", initial_css=".old")
    sel.find(html_v2, domain="x.com", selector_id="thing", initial_css=".old")

    rows = sel.stats()["rows"]
    assert len(rows) == 1
    assert rows[0]["relocate_count"] == 1


# ── Broken: major redesign with no similar element ─────────────────────

def test_major_redesign_returns_broken_status(tmp_db, caplog):
    sel = AdaptiveSelector(db_path=tmp_db)
    html_v1 = (
        '<html><body><div class="price" data-id="42">€42.00</div></body></html>'
    )
    # v2: completely different page — different tag, different attrs,
    # different text. Best similarity should fall below 0.7.
    html_v2 = (
        '<html><body><nav><a href="/login">Sign in</a></nav>'
        '<header>Welcome</header></body></html>'
    )

    sel.find(html_v1, domain="x.com", selector_id="price", initial_css=".price")

    with caplog.at_level(logging.WARNING):
        result = sel.find(
            html_v2, domain="x.com", selector_id="price",
            initial_css=".price",
        )

    assert result.status == "broken"
    assert result.element is None
    assert any("broken" in rec.message for rec in caplog.records)


# ── Persistence across instances ───────────────────────────────────────

def test_stored_selector_persists_across_instances(tmp_db):
    """The whole point of SQLite — close the process, reopen, state
    survives. Pin this so a future refactor doesn't quietly switch to
    in-memory storage."""
    html = '<html><body><div class="x">value</div></body></html>'

    sel1 = AdaptiveSelector(db_path=tmp_db)
    sel1.find(html, domain="d.com", selector_id="thing", initial_css=".x")

    sel2 = AdaptiveSelector(db_path=tmp_db)
    result = sel2.find(html, domain="d.com", selector_id="thing", initial_css=".x")
    assert result.status == "hit"


# ── find_text convenience wrapper ──────────────────────────────────────

def test_find_text_returns_string(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    html = '<html><body><div class="x">€99</div></body></html>'
    text = sel.find_text(html, domain="d.com", selector_id="t", initial_css=".x")
    assert text == "€99"


def test_find_text_returns_none_on_broken(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    text = sel.find_text(
        "<html></html>", domain="d.com", selector_id="t", initial_css=".x",
    )
    assert text is None


# ── Defensive: empty input ─────────────────────────────────────────────

def test_empty_html_returns_broken(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    r = sel.find("", domain="d.com", selector_id="t", initial_css=".x")
    assert r.status == "broken"


# ── stats() snapshot ───────────────────────────────────────────────────

def test_stats_lists_all_stored_selectors(tmp_db):
    sel = AdaptiveSelector(db_path=tmp_db)
    sel.find(
        '<html><body><div class="a">1</div></body></html>',
        domain="x.com", selector_id="one", initial_css=".a",
    )
    sel.find(
        '<html><body><div class="b">2</div></body></html>',
        domain="y.com", selector_id="two", initial_css=".b",
    )
    s = sel.stats()
    assert s["total"] == 2
    domains = {r["domain"] for r in s["rows"]}
    assert domains == {"x.com", "y.com"}
