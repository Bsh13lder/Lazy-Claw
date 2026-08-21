"""Column widths, row heights, auto-fit, merges and freeze panes.

Geometry is what actually makes an agent-written table readable — a bold header
on 88px columns is still a wall of truncated text. Like styles, the keys here
are a contract with the web editor and the Flutter grid, so they're asserted
literally.
"""

from __future__ import annotations

import pytest

from lazyclaw.sheets import geometry as G
from lazyclaw.sheets import snapshot as S


def _wb():
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh")


def _sheet(snap):
    return snap["sheets"]["sh"]


def _grid(rows):
    """A workbook whose A1-down content is ``rows`` (list of row lists)."""
    wb = _wb()
    edits = []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if value is not None:
                edits.append({"row": r, "col": c, "value": value})
    return S.set_cells(wb, edits)


# ───────────────────────── widths / heights ─────────────────────────

def test_set_column_width_writes_columndata_w():
    out = G.set_column_width(_wb(), 1, 140)
    assert _sheet(out)["columnData"]["1"]["w"] == 140


def test_set_row_height_writes_rowdata_h():
    out = G.set_row_height(_wb(), 0, 32)
    assert _sheet(out)["rowData"]["0"]["h"] == 32


def test_width_merges_into_an_existing_entry():
    """A sibling ``hd``/``s``/``custom`` on that column must survive."""
    wb = _wb()
    _sheet(wb)["columnData"] = {"1": {"hd": 0, "s": "s-x", "custom": {"k": 1}}}
    out = G.set_column_width(wb, 1, 140)
    assert _sheet(out)["columnData"]["1"] == {
        "hd": 0, "s": "s-x", "custom": {"k": 1}, "w": 140,
    }


def test_height_clears_auto_height_flag():
    """Univer ignores an explicit ``h`` while ``ia`` (self-adaptive) is on."""
    wb = _wb()
    _sheet(wb)["rowData"] = {"0": {"ia": 1, "ah": 40}}
    out = G.set_row_height(wb, 0, 32)
    assert _sheet(out)["rowData"]["0"]["h"] == 32
    assert _sheet(out)["rowData"]["0"].get("ia") in (None, 0)


@pytest.mark.parametrize("width,expected", [
    (0, G.COL_WIDTH_MIN), (5, G.COL_WIDTH_MIN), (99999, G.COL_WIDTH_MAX),
])
def test_column_width_is_clamped(width, expected):
    assert _sheet(G.set_column_width(_wb(), 0, width))["columnData"]["0"]["w"] \
        == expected


def test_getters_fall_back_to_the_univer_defaults():
    wb = _wb()
    assert G.get_column_width(wb, 0) == G.DEFAULT_COL_WIDTH == 88
    assert G.get_row_height(wb, 0) == G.DEFAULT_ROW_HEIGHT == 24


def test_batch_setters_apply_everything():
    out = G.set_column_widths(_wb(), {0: 120, 2: 200})
    assert _sheet(out)["columnData"]["0"]["w"] == 120
    assert _sheet(out)["columnData"]["2"]["w"] == 200
    assert "1" not in _sheet(out)["columnData"]


def test_setters_do_not_mutate_the_source():
    wb = _wb()
    G.set_column_width(wb, 0, 200)
    G.set_row_height(wb, 0, 60)
    assert "columnData" not in _sheet(wb)
    assert "rowData" not in _sheet(wb)


# ───────────────────────── auto-fit ─────────────────────────────────

def test_auto_fit_sizes_to_the_widest_cell():
    wb = _grid([["Item"], ["A much longer product name"]])
    out = G.auto_fit_columns(wb, [0])
    width = _sheet(out)["columnData"]["0"]["w"]
    assert G.AUTOFIT_MIN_PX <= width <= G.AUTOFIT_MAX_PX
    assert width > G.measure_column_width(_grid([["Item"]]), 0)


def test_auto_fit_clamps_short_and_long():
    assert G.measure_column_width(_grid([["ab"]]), 0) == G.AUTOFIT_MIN_PX
    assert G.measure_column_width(_grid([["x" * 500]]), 0) == G.AUTOFIT_MAX_PX


def test_auto_fit_on_an_empty_column_keeps_the_default():
    assert G.measure_column_width(_wb(), 0) == G.DEFAULT_COL_WIDTH


def test_auto_fit_measures_the_FORMATTED_text():
    """``1234.5678`` under ``#,##0.00`` renders as ``1,234.57`` — 8 chars, not 9.

    Sizing to the raw repr would make every currency column subtly wrong.
    """
    from lazyclaw.sheets import styles as ST

    raw = _grid([[1234.5678]])
    formatted = ST.apply_style_a1(raw, "A1", {"number_format": "#,##0.00"})
    assert G.measure_column_width(formatted, 0) \
        == G.width_for_text_length(len("1,234.57"))


def test_auto_fit_skips_cells_covered_by_a_merge():
    """A merged 3-column banner must not force column A to its max width."""
    wb = _grid([["A very long merged banner title indeed"], ["ok"]])
    merged = G.merge_cells(wb, 0, 0, 0, 2)
    assert G.measure_column_width(merged, 0) \
        == G.width_for_text_length(len("ok"))


def test_auto_fit_all_columns_when_none_given():
    wb = _grid([["Name", "Value"], ["Alexandra", 1]])
    out = G.auto_fit_columns(wb)
    assert set(_sheet(out)["columnData"]) == {"0", "1"}


def test_auto_fit_ignores_columns_past_the_content():
    out = G.auto_fit_columns(_grid([["a"]]), [0, 5])
    assert "5" not in _sheet(out)["columnData"]


# ───────────────────────── merges ───────────────────────────────────

def test_merge_writes_an_inclusive_rect():
    """`typedef.d.ts:330` calls endRow exclusive but `:352` shows A1:B2 ==
    {0,0,1,1} — inclusive wins, and Univer renders it that way."""
    out = G.merge_cells(_wb(), 0, 0, 1, 1)
    assert _sheet(out)["mergeData"] == [
        {"startRow": 0, "startColumn": 0, "endRow": 1, "endColumn": 1}
    ]


def test_merge_drops_an_overlapping_rect():
    wb = G.merge_cells(_wb(), 0, 0, 0, 2)
    out = G.merge_cells(wb, 0, 1, 0, 3)
    assert len(_sheet(out)["mergeData"]) == 1
    assert _sheet(out)["mergeData"][0]["endColumn"] == 3


def test_merge_keeps_a_disjoint_rect():
    wb = G.merge_cells(_wb(), 0, 0, 0, 1)
    out = G.merge_cells(wb, 5, 0, 5, 1)
    assert len(_sheet(out)["mergeData"]) == 2


def test_unmerge_matches_by_containment_not_equality():
    """"Unmerge B1" must dissolve the A1:C1 merge that contains B1."""
    wb = G.merge_cells(_wb(), 0, 0, 0, 2)
    out = G.unmerge_cells(wb, 0, 1)
    assert _sheet(out)["mergeData"] == []


def test_unmerge_outside_any_merge_is_a_no_op():
    wb = G.merge_cells(_wb(), 0, 0, 0, 2)
    out = G.unmerge_cells(wb, 4, 4)
    assert len(_sheet(out)["mergeData"]) == 1


def test_merged_range_at_reports_the_rect():
    wb = G.merge_cells(_wb(), 2, 1, 3, 4)
    assert G.merged_range_at(wb, 3, 2) == {
        "startRow": 2, "startColumn": 1, "endRow": 3, "endColumn": 4
    }
    assert G.merged_range_at(wb, 0, 0) is None


def test_merge_does_not_delete_the_covered_values():
    """Univer hides them; an unmerge must bring them back."""
    wb = _grid([["a", "b", "c"]])
    out = G.merge_cells(wb, 0, 0, 0, 2)
    assert S.cell_display(S.get_cell(out, 0, 1)) == "b"


# ───────────────────────── freeze ───────────────────────────────────

def test_freeze_rows_writes_ifreeze():
    out = G.freeze_panes(_wb(), rows=1)
    assert _sheet(out)["freeze"] == {
        "xSplit": 0, "ySplit": 1, "startRow": 1, "startColumn": 0
    }


def test_freeze_both_axes():
    out = G.freeze_panes(_wb(), rows=2, cols=1)
    assert _sheet(out)["freeze"] == {
        "xSplit": 1, "ySplit": 2, "startRow": 2, "startColumn": 1
    }


def test_freeze_zero_removes_the_key():
    """Mirrors the Flutter toggle — absent means 'not frozen', not zeros."""
    wb = G.freeze_panes(_wb(), rows=1)
    out = G.freeze_panes(wb, rows=0, cols=0)
    assert "freeze" not in _sheet(out)


def test_unfreeze_removes_the_key():
    wb = G.freeze_panes(_wb(), rows=1)
    assert "freeze" not in _sheet(G.unfreeze(wb))


def test_freeze_counts_are_clamped():
    out = G.freeze_panes(_wb(), rows=99999)
    assert _sheet(out)["freeze"]["ySplit"] == G.FREEZE_MAX


# ───────────────────────── worksheet targeting ──────────────────────

def test_geometry_targets_a_named_worksheet():
    wb = _wb()
    wb["sheets"]["sh2"] = {
        "id": "sh2", "name": "Second", "rowCount": 10, "columnCount": 5,
        "cellData": {},
    }
    wb["sheetOrder"].append("sh2")
    out = G.set_column_width(wb, 0, 150, sheet="Second")
    assert out["sheets"]["sh2"]["columnData"]["0"]["w"] == 150
    assert "columnData" not in out["sheets"]["sh"]
