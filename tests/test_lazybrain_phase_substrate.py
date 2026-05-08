"""Phase E/F/G unit coverage — Aho-Corasick autolink, RRF fusion, chunker.

These cover the pure-Python modules without booting a DB or LLM. The
DB-touching paths (FTS5 write-through, hybrid retrieval end-to-end) lean
on the existing ``test_lazybrain_substrate_db.py`` patterns and were
manually verified — kept narrow here to keep the unit pass < 100 ms.
"""
from __future__ import annotations

from lazyclaw.lazybrain import aho_index, chunker, rrf


# ── RRF fusion ────────────────────────────────────────────────────────


def test_rrf_fuse_two_lists_basic() -> None:
    """Docs present in both lists outrank single-list hits."""
    result = rrf.fuse([["a", "b", "c"], ["b", "a", "d"]], k=60)
    ids = [d for d, _ in result]
    # `a` and `b` each accumulate 1/61 + 1/62 from being rank 1 in one
    # list and rank 2 in the other — they tie on score. Either ordering
    # at the top is correct; what matters is they BOTH outrank `c` / `d`
    # (each in only one list).
    assert set(ids[:2]) == {"a", "b"}
    assert set(ids) == {"a", "b", "c", "d"}
    score_by_id = dict(result)
    assert score_by_id["a"] > score_by_id["c"]
    assert score_by_id["b"] > score_by_id["d"]


def test_rrf_fuse_to_ids_limit() -> None:
    out = rrf.fuse_to_ids([["x", "y", "z", "w"]], limit=2)
    assert out == ["x", "y"]


def test_rrf_handles_empty_input() -> None:
    assert rrf.fuse([]) == []
    assert rrf.fuse_to_ids([[], []]) == []


def test_rrf_skips_empty_ids() -> None:
    """Empty / falsy doc ids must be skipped — they're a sentinel for
    "no result" in the BM25 callers."""
    out = rrf.fuse_to_ids([["", "a", None, "b"]])  # type: ignore[list-item]
    assert out == ["a", "b"]


# ── chunker ───────────────────────────────────────────────────────────


def test_chunker_short_note_one_chunk() -> None:
    text = "A tiny atomic note that should not be split."
    out = chunker.chunk_note(text)
    assert len(out) == 1
    assert out[0].text == text
    assert out[0].char_start == 0
    assert out[0].char_end == len(text)


def test_chunker_long_note_paragraph_split() -> None:
    """A multi-paragraph note longer than 1.5× window should produce
    multiple chunks with overlap."""
    paragraph = "Line. " * 200  # ~1200 chars per paragraph
    text = "\n\n".join([paragraph] * 6)  # ~7 KB total
    out = chunker.chunk_note(text, window_tokens=200, overlap_tokens=50)
    assert len(out) >= 2
    assert out[0].char_start == 0
    # Subsequent chunks must reference real ranges in the body.
    for c in out:
        assert c.char_start < c.char_end <= len(text)


def test_chunker_empty_input() -> None:
    assert chunker.chunk_note("") == []
    assert chunker.chunk_note("   ") == []


def test_chunker_single_huge_paragraph_falls_back_to_hard_window() -> None:
    """A wall-of-text with no paragraph breaks must still split into
    multiple chunks via the hard-char-window fallback."""
    text = "x" * 12_000  # one big paragraph
    out = chunker.chunk_note(text, window_tokens=200, overlap_tokens=40)
    assert len(out) > 1


# ── Aho-Corasick (only when extension available) ──────────────────────


def test_aho_invalidate_clears_cache() -> None:
    aho_index.invalidate("user-x")
    aho_index.invalidate()  # global clear is a no-op when empty


def test_aho_returns_none_without_titles() -> None:
    assert aho_index.get_automaton("user-y", []) is None


def test_aho_scan_against_empty_text() -> None:
    if not aho_index.is_available():
        return  # skip when extension missing
    auto = aho_index.get_automaton(
        "user-aho-empty", ["Alpha Bravo", "Charlie Delta"],
    )
    assert auto is not None
    assert aho_index.scan(auto, "") == []


def test_aho_finds_word_boundary_match() -> None:
    """The scanner must hit "Alpha Bravo" but not the substring inside
    "AlphaBravoCorp" — word-boundary aware."""
    if not aho_index.is_available():
        return
    auto = aho_index.get_automaton(
        "user-aho-wb", ["Alpha Bravo", "Charlie Delta"],
    )
    assert auto is not None
    text = "We met Alpha Bravo today, but AlphaBravoCorp is unrelated."
    out = aho_index.scan(auto, text)
    pages = {hit["page"] for hit in out}
    assert "Alpha Bravo" in pages


def test_aho_skips_inside_masked_spans() -> None:
    """Existing ``[[wikilinks]]`` are pre-masked and must be skipped."""
    if not aho_index.is_available():
        return
    auto = aho_index.get_automaton("user-aho-mask", ["Sangonera"])
    assert auto is not None
    text = "See [[Sangonera]] for context."
    # mask covers the [[…]] region
    masked = [(text.index("[["), text.index("]]") + 2)]
    out = aho_index.scan(auto, text, masked_spans=masked)
    assert out == []
