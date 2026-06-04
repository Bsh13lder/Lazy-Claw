"""Hyperlink support for the Univer-document text model (lazyclaw/docs/snapshot.py).

Univer stores a hyperlink as a ``customRange`` over a span of ``dataStream`` that
is bracketed by the sentinel control characters ``CUSTOM_RANGE_START`` (``\\u001F``)
and ``CUSTOM_RANGE_END`` (``\\u001E``). These tests pin the on-the-wire shape, the
sentinel-stripping in plain-text reads, the run round-trip used by the docx
exporter, and the markdown / make-link convenience builders.
"""

from __future__ import annotations

import copy

from lazyclaw.docs import snapshot as D


START = D.CUSTOM_RANGE_START
END = D.CUSTOM_RANGE_END


# ───────────────────────── build_body_with_runs ─────────────────────────

def test_single_link_paragraph_datastream_and_customrange():
    body = D.build_body_with_runs([[{"text": "site", "url": "https://x.io"}]])
    assert body["dataStream"] == f"{START}site{END}\r\n"
    # sentinel positions
    ds = body["dataStream"]
    assert ds[0] == START and ds[5] == END
    cr = body["customRanges"]
    assert len(cr) == 1
    assert cr[0]["startIndex"] == 0
    assert cr[0]["endIndex"] == 5
    assert cr[0]["rangeType"] == 0  # CustomRangeType.HYPERLINK
    assert cr[0]["properties"] == {"url": "https://x.io"}
    assert isinstance(cr[0]["rangeId"], str) and cr[0]["rangeId"]
    # paragraph terminator + section break bookkeeping
    assert body["paragraphs"] == [{"startIndex": 6}]
    assert body["sectionBreaks"] == [{"startIndex": 7}]


def test_mixed_runs_indices():
    body = D.build_body_with_runs(
        [[{"text": "Visit "}, {"text": "site", "url": "u"}, {"text": " now"}]]
    )
    assert body["dataStream"] == f"Visit {START}site{END} now\r\n"
    cr = body["customRanges"][0]
    assert cr["startIndex"] == 6
    assert cr["endIndex"] == 11
    assert cr["properties"]["url"] == "u"
    assert body["paragraphs"] == [{"startIndex": 16}]


def test_plain_paragraph_has_no_customranges():
    body = D.build_body_with_runs([[{"text": "just text"}]])
    assert body["dataStream"] == "just text\r\n"
    assert body["customRanges"] == []


def test_empty_paragraph_collapses_to_break():
    body = D.build_body_with_runs([[]])
    assert body["dataStream"] == "\r\n"
    assert body["customRanges"] == []
    assert body["paragraphs"] == [{"startIndex": 0}]


def test_multi_paragraph_link_ids_unique():
    body = D.build_body_with_runs(
        [
            [{"text": "a", "url": "1"}],
            [{"text": "b", "url": "2"}],
        ]
    )
    ids = [c["rangeId"] for c in body["customRanges"]]
    assert len(ids) == len(set(ids)) == 2


# ───────────────────────── reads strip sentinels ────────────────────────

def test_get_paragraphs_strips_sentinels():
    snap = {"id": "d", "body": D.build_body_with_runs([[{"text": "site", "url": "u"}]])}
    assert D.get_paragraphs(snap) == ["site"]
    assert START not in D.get_text(snap)
    assert END not in D.get_text(snap)


def test_get_text_includes_link_label():
    snap = {
        "id": "d",
        "body": D.build_body_with_runs(
            [[{"text": "Visit "}, {"text": "site", "url": "u"}, {"text": " now"}]]
        ),
    }
    assert D.get_text(snap) == "Visit site now"


# ───────────────────────── get_paragraph_runs round-trip ────────────────

def test_get_paragraph_runs_round_trip():
    paras = [
        [{"text": "Visit "}, {"text": "site", "url": "https://x"}, {"text": "."}],
        [{"text": "plain second"}],
    ]
    snap = {"id": "d", "body": D.build_body_with_runs(paras)}
    out = D.get_paragraph_runs(snap)
    assert out == paras


def test_get_paragraph_runs_plain_doc():
    snap = D.set_text(D.blank_document("T", doc_id="d"), "one\ntwo")
    assert D.get_paragraph_runs(snap) == [[{"text": "one"}], [{"text": "two"}]]


def test_get_paragraph_runs_empty_doc():
    snap = D.blank_document("T", doc_id="d")
    assert D.get_paragraph_runs(snap) == [[]]


# ───────────────────────── append_paragraph_with_runs ───────────────────

def test_append_runs_to_blank_replaces_empty():
    base = D.blank_document("T", doc_id="d")
    out = D.append_paragraph_with_runs(base, [{"text": "Hi ", }, {"text": "site", "url": "u"}])
    assert D.get_paragraphs(out) == ["Hi site"]
    assert out["body"]["customRanges"][0]["properties"]["url"] == "u"


def test_append_runs_after_content_preserves_existing_link():
    first = D.append_paragraph_with_runs(
        D.blank_document("T", doc_id="d"), [{"text": "one", "url": "u1"}]
    )
    out = D.append_paragraph_with_runs(first, [{"text": "two", "url": "u2"}])
    assert D.get_paragraphs(out) == ["one", "two"]
    urls = [c["properties"]["url"] for c in out["body"]["customRanges"]]
    assert urls == ["u1", "u2"]


def test_append_runs_is_immutable():
    base = D.set_text(D.blank_document("T", doc_id="d"), "x")
    before = copy.deepcopy(base)
    D.append_paragraph_with_runs(base, [{"text": "y", "url": "u"}])
    assert base == before


# ───────────────────────── markdown / make-link builders ────────────────

def test_runs_from_markdown_parses_links():
    runs = D.runs_from_markdown("See [my site](https://x.io) for more")
    assert runs == [
        {"text": "See "},
        {"text": "my site", "url": "https://x.io"},
        {"text": " for more"},
    ]


def test_runs_from_markdown_no_links():
    assert D.runs_from_markdown("plain text") == [{"text": "plain text"}]


def test_make_link_runs_splits_around_label():
    runs = D.make_link_runs("Check my site here", "site", "https://x")
    assert runs == [
        {"text": "Check my "},
        {"text": "site", "url": "https://x"},
        {"text": " here"},
    ]


def test_make_link_runs_label_absent_appends():
    runs = D.make_link_runs("Check it out", "portfolio", "https://x")
    assert runs[-1] == {"text": "portfolio", "url": "https://x"}


def test_make_link_runs_no_url_is_plain():
    assert D.make_link_runs("hello", None, None) == [{"text": "hello"}]
