"""Row / column insert + delete.

The mechanical half of restructuring: shifting cellData, rowData/columnData,
mergeData, the hyperlink resource and the row/column counts. Formula references
are deliberately NOT adjusted — the last test in this file pins that as a
documented limitation rather than an accident.
"""

from __future__ import annotations

import json

import pytest

from lazyclaw.sheets import geometry as G
from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets import structure as St


def _wb(rows=None):
    wb = S.blank_workbook("T", workbook_id="wb", sheet_id="sh")
    edits = []
    for r, row in enumerate(rows or [["a"], ["b"], ["c"]]):
        for c, value in enumerate(row):
            if value is not None:
                edits.append({"row": r, "col": c, "value": value})
    return S.set_cells(wb, edits)


def _col(snap, c=0):
    return [S.cell_display(S.get_cell(snap, r, c)) for r in range(4)]


# ───────────────────────── rows ─────────────────────────────────────

def test_insert_row_pushes_everything_down():
    out = St.insert_rows(_wb(), 1)
    assert _col(out) == ["a", "", "b", "c"]


def test_insert_multiple_rows():
    out = St.insert_rows(_wb(), 0, 2)
    assert _col(out) == ["", "", "a", "b"]


def test_delete_row_pulls_everything_up():
    out = St.delete_rows(_wb(), 1)
    assert _col(out) == ["a", "c", "", ""]


def test_delete_multiple_rows():
    out = St.delete_rows(_wb(), 0, 2)
    assert _col(out) == ["c", "", "", ""]


def test_insert_does_not_mutate_the_source():
    wb = _wb()
    St.insert_rows(wb, 0)
    assert _col(wb) == ["a", "b", "c", ""]


def test_row_count_tracks_the_change():
    wb = _wb()
    before = wb["sheets"]["sh"]["rowCount"]
    assert St.insert_rows(wb, 0, 3)["sheets"]["sh"]["rowCount"] == before + 3
    assert St.delete_rows(wb, 0, 2)["sheets"]["sh"]["rowCount"] == before - 2


# ───────────────────────── columns ──────────────────────────────────

def test_insert_column_pushes_everything_right():
    wb = _wb([["a", "b", "c"]])
    out = St.insert_columns(wb, 1)
    row = [S.cell_display(S.get_cell(out, 0, c)) for c in range(4)]
    assert row == ["a", "", "b", "c"]


def test_delete_column_pulls_everything_left():
    wb = _wb([["a", "b", "c"]])
    out = St.delete_columns(wb, 0)
    row = [S.cell_display(S.get_cell(out, 0, c)) for c in range(3)]
    assert row == ["b", "c", ""]


# ───────────────────────── geometry moves with the data ─────────────

def test_row_heights_move_with_their_rows():
    wb = G.set_row_height(_wb(), 2, 44)
    out = St.insert_rows(wb, 0)
    assert G.get_row_height(out, 3) == 44
    assert G.get_row_height(out, 2) == G.DEFAULT_ROW_HEIGHT


def test_column_widths_move_with_their_columns():
    wb = G.set_column_width(_wb([["a", "b"]]), 1, 200)
    out = St.insert_columns(wb, 0)
    assert G.get_column_width(out, 2) == 200


def test_a_deleted_rows_height_is_dropped():
    wb = G.set_row_height(_wb(), 1, 44)
    out = St.delete_rows(wb, 1)
    assert G.get_row_height(out, 1) == G.DEFAULT_ROW_HEIGHT


# ───────────────────────── merges ───────────────────────────────────

def test_a_merge_below_an_insert_moves_down():
    wb = G.merge_cells(_wb(), 2, 0, 2, 2)
    out = St.insert_rows(wb, 0)
    assert G.merged_range_at(out, 3, 1) is not None
    assert G.merged_range_at(out, 2, 1) is None


def test_a_merge_straddling_an_insert_grows():
    wb = G.merge_cells(_wb(), 0, 0, 2, 0)
    out = St.insert_rows(wb, 1)
    rect = G.merged_range_at(out, 0, 0)
    assert rect is not None and rect["endRow"] == 3


def test_a_merge_inside_a_deleted_band_is_dropped():
    wb = G.merge_cells(_wb(), 1, 0, 1, 2)
    out = St.delete_rows(wb, 1)
    assert out["sheets"]["sh"].get("mergeData") in (None, [])


# ───────────────────────── hyperlinks ───────────────────────────────

def _links(snap):
    for res in snap.get("resources") or []:
        if res.get("name") == "SHEET_HYPER_LINK_PLUGIN":
            return json.loads(res["data"]).get("sh") or []
    return []


def test_a_link_below_an_insert_follows_its_cell():
    wb = S.set_cell_link(_wb(), 2, 0, "https://example.com", display="site")
    assert _links(wb)[0]["row"] == 2
    out = St.insert_rows(wb, 0)
    assert _links(out)[0]["row"] == 3, "the link would point at the wrong cell"


def test_a_link_in_a_deleted_row_is_removed():
    wb = S.set_cell_link(_wb(), 1, 0, "https://example.com", display="site")
    out = St.delete_rows(wb, 1)
    assert _links(out) == []


def test_a_link_above_an_insert_stays_put():
    wb = S.set_cell_link(_wb(), 0, 0, "https://example.com", display="site")
    assert _links(St.insert_rows(wb, 2))[0]["row"] == 0


# ───────────────────────── styles ride along ────────────────────────

def test_a_styled_cell_keeps_its_style_after_a_shift():
    from lazyclaw.sheets import styles as ST

    wb = ST.apply_style_a1(_wb(), "A3", {"bold": True})
    out = St.insert_rows(wb, 0)
    assert ST.get_style_view(out, 3, 0) == {"bold": True}


# ───────────────────────── validation ───────────────────────────────

@pytest.mark.parametrize("kwargs", [
    {"at": -1, "count": 1},
    {"at": 0, "count": 0},
    {"at": 0, "count": St.MAX_SHIFT + 1},
])
def test_bad_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        St.insert_rows(_wb(), kwargs["at"], kwargs["count"])


# ───────────────────────── the documented limitation ────────────────

def test_formula_references_are_NOT_adjusted():
    """Pinned deliberately.

    A spreadsheet would rewrite `=SUM(A1:A3)` to `=SUM(A1:A4)` after an insert
    above it. Doing that needs a real A1 tokeniser (string literals,
    sheet-qualified refs, INDIRECT/OFFSET), and a half-right rewrite produces a
    silently-wrong total the user cannot see. The skill states the caveat; this
    test makes sure the behaviour never changes by accident.
    """
    wb = S.set_cells(_wb(), [{"cell": "B1", "formula": "=SUM(A1:A3)"}])
    out = St.insert_rows(wb, 0)
    assert S.get_cell(out, 1, 1)["f"] == "=SUM(A1:A3)"


def test_has_formulas_detects_whether_the_caveat_is_worth_showing():
    assert St.has_formulas(_wb()) is False
    assert St.has_formulas(
        S.set_cells(_wb(), [{"cell": "B1", "formula": "=A1"}])
    ) is True
