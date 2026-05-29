"""Unit tests for the pure Univer-document helpers (lazyclaw/docs/snapshot.py).

These functions carry the whole text model, so they're tested hard: blank-doc
validity, paragraph/dataStream bookkeeping, plain-text round-trips, append
semantics, immutability of every mutator, and robustness to malformed input.
"""

from __future__ import annotations

import copy

from lazyclaw.docs import snapshot as D


# ───────────────────────── document factory ─────────────────────────

def test_blank_document_is_valid_and_empty():
    doc = D.blank_document("Letter", doc_id="doc-x")
    assert doc["id"] == "doc-x"
    assert doc["name"] == "Letter"
    body = doc["body"]
    assert body["dataStream"] == "\r\n"
    assert body["paragraphs"] == [{"startIndex": 0}]
    assert D.get_paragraphs(doc) == [""]
    assert D.get_text(doc) == ""
    assert D.is_empty(doc) is True


def test_blank_document_defaults_name():
    assert D.blank_document("   ")["name"] == "Untitled doc"


def test_blank_document_generates_id_when_omitted():
    doc = D.blank_document("X")
    assert doc["id"].startswith("doc-")


# ───────────────────────── set_text / round-trip ────────────────────

def test_set_text_round_trips_via_get_text():
    base = D.blank_document("T", doc_id="d")
    text = "First paragraph.\nSecond paragraph.\nThird."
    out = D.set_text(base, text)
    assert D.get_text(out) == text
    assert D.get_paragraphs(out) == ["First paragraph.", "Second paragraph.", "Third."]


def test_set_text_single_line():
    out = D.set_text(D.blank_document("T"), "Just one line")
    assert D.get_paragraphs(out) == ["Just one line"]
    assert D.get_text(out) == "Just one line"


def test_set_text_empty_string():
    out = D.set_text(D.blank_document("T"), "")
    assert D.get_paragraphs(out) == [""]
    assert D.is_empty(out) is True


def test_set_text_datastream_terminators():
    out = D.set_text(D.blank_document("T"), "a\nb")
    stream = out["body"]["dataStream"]
    assert stream == "a\rb\r\n"
    # startIndex points at each paragraph's "\r"
    assert out["body"]["paragraphs"] == [{"startIndex": 1}, {"startIndex": 3}]
    assert stream[1] == "\r"
    assert stream[3] == "\r"
    assert stream[-1] == "\n"


def test_set_text_preserves_top_level_keys():
    base = D.blank_document("Keep", doc_id="keep-id")
    base["documentStyle"] = {"pageSize": {"width": 595}}
    out = D.set_text(base, "hi")
    assert out["id"] == "keep-id"
    assert out["name"] == "Keep"
    assert out["documentStyle"] == {"pageSize": {"width": 595}}


def test_set_text_is_immutable():
    base = D.blank_document("T", doc_id="d")
    before = copy.deepcopy(base)
    D.set_text(base, "mutate me")
    assert base == before  # original untouched


def test_set_text_sanitizes_embedded_control_chars():
    # Stray \r / \n inside a single paragraph must not corrupt indexing.
    out = D.set_text(D.blank_document("T"), "a\rb")  # no real newline split
    paras = D.get_paragraphs(out)
    assert len(paras) == 1
    assert "\r" not in paras[0]


# ───────────────────────── append_paragraph ─────────────────────────

def test_append_to_blank_replaces_empty_paragraph():
    base = D.blank_document("T")
    out = D.append_paragraph(base, "Hello")
    assert D.get_paragraphs(out) == ["Hello"]


def test_append_after_content_adds_new_paragraph():
    doc = D.set_text(D.blank_document("T"), "Line one")
    out = D.append_paragraph(doc, "Line two")
    assert D.get_paragraphs(out) == ["Line one", "Line two"]
    assert D.get_text(out) == "Line one\nLine two"


def test_append_is_immutable():
    doc = D.set_text(D.blank_document("T"), "x")
    before = copy.deepcopy(doc)
    D.append_paragraph(doc, "y")
    assert doc == before


def test_append_multiple_times():
    doc = D.blank_document("T")
    doc = D.append_paragraph(doc, "a")
    doc = D.append_paragraph(doc, "b")
    doc = D.append_paragraph(doc, "c")
    assert D.get_paragraphs(doc) == ["a", "b", "c"]


# ───────────────────────── robustness ───────────────────────────────

def test_get_paragraphs_on_garbage():
    assert D.get_paragraphs({}) == []
    assert D.get_paragraphs({"body": "not a dict"}) == []
    assert D.get_paragraphs({"body": {}}) == []
    assert D.get_paragraphs({"body": {"dataStream": ""}}) == []


def test_get_paragraphs_falls_back_to_stream_split():
    # Body with a dataStream but no paragraphs metadata.
    snap = {"body": {"dataStream": "one\rtwo\r\n"}}
    assert D.get_paragraphs(snap) == ["one", "two"]


def test_get_paragraphs_falls_back_on_bad_indices():
    snap = {
        "body": {
            "dataStream": "one\rtwo\r\n",
            "paragraphs": [{"startIndex": 999}],  # out of range → fallback
        }
    }
    assert D.get_paragraphs(snap) == ["one", "two"]


def test_get_text_on_garbage_is_empty():
    assert D.get_text({}) == ""
    assert D.get_text({"body": None}) == ""
