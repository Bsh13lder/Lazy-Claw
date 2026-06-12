"""Tests for hyperlink resource helpers in lazyclaw/sheets/snapshot.py.

Covers: _link_data, get_sheet_links, set_cell_link, remove_cell_link,
and convert_urls_to_links (plus immutability guarantees).
"""

from __future__ import annotations

import copy
import json

import pytest

from lazyclaw.sheets import snapshot as S


# ───────────────────────── fixtures ─────────────────────────────────


def _wb() -> dict:
    """Deterministic blank workbook used across all tests."""
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh")


# ───────────────────────── 1. get_sheet_links – no resources ─────────


def test_get_sheet_links_no_resources():
    """Workbook with no resources key → empty list."""
    wb = _wb()
    assert S.get_sheet_links(wb) == []


# ───────────────────────── 2. set_cell_link basics ───────────────────


def test_set_cell_link_creates_resource_and_entry():
    """set_cell_link creates the resource entry with correct fields."""
    wb = _wb()
    out = S.set_cell_link(wb, 0, 1, "https://x.com")
    links = S.get_sheet_links(out)
    assert len(links) == 1
    entry = links[0]
    assert entry["row"] == 0
    assert entry["column"] == 1
    assert entry["payload"] == "https://x.com"
    assert entry["id"].startswith("l-")


def test_set_cell_link_with_display_sets_cell_text():
    """display kwarg writes the text into the cell."""
    wb = _wb()
    out = S.set_cell_link(wb, 2, 3, "https://example.org", display="Click me")
    cell = S.get_cell(out, 2, 3)
    assert cell is not None
    assert cell.get("v") == "Click me"


# ───────────────────────── 3. upsert at same cell ────────────────────


def test_set_cell_link_upsert_same_cell():
    """Two calls at the same (row, col) → exactly one entry, latest URL wins."""
    wb = _wb()
    out = S.set_cell_link(wb, 0, 0, "https://first.com")
    out = S.set_cell_link(out, 0, 0, "https://second.com")
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://second.com"


# ───────────────────────── 4. remove_cell_link ───────────────────────


def test_remove_cell_link_removes_only_target():
    """remove_cell_link removes just the target entry, leaving others."""
    wb = _wb()
    out = S.set_cell_link(wb, 0, 0, "https://a.com")
    out = S.set_cell_link(out, 1, 0, "https://b.com")
    out = S.remove_cell_link(out, 0, 0)
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://b.com"


# ───────────────────────── 5. converter – bare URL ───────────────────


def test_converter_bare_url():
    """A cell containing only a URL gets linked; text is unchanged."""
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "https://example.com"}])
    out, count = S.convert_urls_to_links(wb)
    assert count == 1
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://example.com"
    # Text must remain unchanged
    cell = S.get_cell(out, 0, 0)
    assert cell is not None
    assert cell.get("v") == "https://example.com"


# ───────────────────────── 6. converter – markdown link ─────────────


def test_converter_markdown_link():
    """A cell with [label](url) becomes label text + link entry."""
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "[Docs](https://d.io)"}])
    out, count = S.convert_urls_to_links(wb)
    assert count == 1
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://d.io"
    cell = S.get_cell(out, 0, 0)
    assert cell is not None
    assert cell.get("v") == "Docs"


# ───────────────────────── 7. converter – trailing punct ─────────────


def test_converter_trailing_dot_stripped_from_url():
    """Trailing punctuation is stripped from the URL stored in payload."""
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "https://x.com."}])
    out, count = S.convert_urls_to_links(wb)
    assert count == 1
    links = S.get_sheet_links(out)
    assert links[0]["payload"] == "https://x.com"


# ───────────────────────── 8. converter – skips ──────────────────────


def test_converter_skips_formula_numeric_and_existing_link():
    """Cells with formulas, non-string values, or existing links are skipped."""
    wb = _wb()
    # formula cell
    wb = S.set_cell(wb, 0, 0, formula="SUM(A1:A2)")
    # numeric cell
    wb = S.set_cell(wb, 1, 0, value=42)
    # text URL then pre-link
    wb = S.set_cells(wb, [{"row": 2, "col": 0, "value": "https://already.com"}])
    wb = S.set_cell_link(wb, 2, 0, "https://already.com")

    out, count = S.convert_urls_to_links(wb)
    assert count == 0


# ───────────────────────── 9. converter idempotent ───────────────────


def test_converter_idempotent():
    """Running the converter twice: second pass adds 0 links."""
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "https://example.com"}])
    out, count1 = S.convert_urls_to_links(wb)
    _, count2 = S.convert_urls_to_links(out)
    assert count1 == 1
    assert count2 == 0


# ───────────────────────── 10. converter multi-sheet ─────────────────


def test_converter_multi_sheet_links_under_correct_subunit_id():
    """Links land under each sheet's own subUnitId in the resource."""
    wb = S.blank_workbook("Multi", workbook_id="wb2", sheet_id="s1")
    # Add second sheet manually
    wb = copy.deepcopy(wb)
    wb["sheetOrder"].append("s2")
    wb["sheets"]["s2"] = {
        "id": "s2",
        "name": "Sheet2",
        "rowCount": 100,
        "columnCount": 10,
        "cellData": {},
    }
    # Place URLs on both sheets
    wb = S.set_cells(wb, [{"row": 0, "col": 0, "value": "https://sheet1.com"}], sheet="s1")
    wb = S.set_cells(wb, [{"row": 0, "col": 0, "value": "https://sheet2.com"}], sheet="s2")

    out, count = S.convert_urls_to_links(wb)
    assert count == 2

    data = S._link_data(out)
    assert "s1" in data and "s2" in data
    assert data["s1"][0]["payload"] == "https://sheet1.com"
    assert data["s2"][0]["payload"] == "https://sheet2.com"


# ───────────────────────── 11. immutability ──────────────────────────


def test_set_cell_link_does_not_mutate_original():
    """set_cell_link must not alter the input snapshot."""
    wb = _wb()
    original = copy.deepcopy(wb)
    _ = S.set_cell_link(wb, 0, 0, "https://mutate-check.com")
    assert wb == original


def test_converter_does_not_mutate_original():
    """convert_urls_to_links must not alter the input snapshot."""
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "https://mutate.io"}])
    original = copy.deepcopy(wb)
    _ = S.convert_urls_to_links(wb)
    assert wb == original


# ───────────────────────── 12. corrupted resource data ───────────────


def test_corrupted_resource_data_returns_empty_and_set_still_works():
    """Corrupted JSON in resource data → _link_data returns {} without raising;
    set_cell_link then creates a fresh valid resource."""
    wb = _wb()
    wb["resources"] = [{"name": S._LINK_RESOURCE, "data": "not json"}]
    assert S._link_data(wb) == {}
    out = S.set_cell_link(wb, 0, 0, "https://recovery.com")
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://recovery.com"


# ───────────────────────── 13. mixed-text URL not converted ──────────


def test_mixed_text_url_not_converted():
    """A cell like 'see https://x.com today' is NOT converted to a link."""
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "see https://x.com today"}])
    out, count = S.convert_urls_to_links(wb)
    assert count == 0
    assert S.get_sheet_links(out) == []


# ───────────────────────── 14. stray elements in resources ───────────


def test_link_data_skips_non_dict_resources():
    """_link_data must not crash on non-dict elements in the resources list."""
    wb = _wb()
    # Mix non-dict "stray" with the real plugin entry.
    wb["resources"] = [
        {"name": "OTHER"},
        "stray",
        {"name": S._LINK_RESOURCE, "data": json.dumps({"sh": [{"id": "l-abc", "row": 0, "column": 0, "payload": "https://ok.com"}]})},
    ]
    data = S._link_data(wb)
    assert data == {"sh": [{"id": "l-abc", "row": 0, "column": 0, "payload": "https://ok.com"}]}


def test_write_link_data_skips_non_dict_resources():
    """_write_link_data must not crash on non-dict elements when scanning resources."""
    wb = _wb()
    wb["resources"] = [
        "stray_string",
        42,
        {"name": "OTHER"},
        {"name": S._LINK_RESOURCE, "data": "{}"},
    ]
    # Should update the existing entry without raising.
    out = copy.deepcopy(wb)
    new_data = {"sh": [{"id": "l-test", "row": 1, "column": 2, "payload": "https://written.com"}]}
    S._write_link_data(out, new_data)
    assert json.loads(out["resources"][3]["data"]) == new_data


# ───────────────────────── 15. string row/col coercion ───────────────


def test_set_cell_link_coerces_string_row_col_upsert():
    """set_cell_link with string row/col coerces to int; same cell → 1 entry."""
    wb = _wb()
    out = S.set_cell_link(wb, 0, 0, "https://first.com")
    # Call again with string "0", "0" — should be treated as the same cell.
    out = S.set_cell_link(out, "0", "0", "https://second.com")  # type: ignore[arg-type]
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://second.com"


def test_remove_cell_link_coerces_string_row_col():
    """remove_cell_link with string row/col coerces to int correctly."""
    wb = _wb()
    out = S.set_cell_link(wb, 0, 0, "https://coerce.com")
    out = S.remove_cell_link(out, "0", "0")  # type: ignore[arg-type]
    assert S.get_sheet_links(out) == []


# ───────────────────────── 16. near-miss markdown (spaces in label) ──


def test_converter_markdown_link_with_spaces_in_label():
    """[ label ](url) — spaces inside brackets are matched by [^\\]]+.

    The current regex _MD_LINK_RE uses [^\\]]+ which matches spaces, so
    '[ label ](url)' DOES match and the display text is ' label ' (with spaces).
    This test PINS that behaviour.
    """
    wb = S.set_cells(_wb(), [{"row": 0, "col": 0, "value": "[ label ](https://spaced.io)"}])
    out, count = S.convert_urls_to_links(wb)
    # The regex matches → link is created.
    assert count == 1
    links = S.get_sheet_links(out)
    assert len(links) == 1
    assert links[0]["payload"] == "https://spaced.io"
    # Display text is ' label ' (spaces preserved — that's what the regex captures).
    cell = S.get_cell(out, 0, 0)
    assert cell is not None
    assert cell.get("v") == " label "


# ───────────────────────── 17. perf-shaped scale test ───────────────


def test_converter_200_url_cells():
    """convert_urls_to_links handles 200 URL cells and yields exactly 200 links."""
    wb = _wb()
    edits = [
        {"row": i, "col": 0, "value": f"https://example.com/page/{i}"}
        for i in range(200)
    ]
    wb = S.set_cells(wb, edits)
    out, count = S.convert_urls_to_links(wb)
    assert count == 200
    assert len(S.get_sheet_links(out)) == 200
