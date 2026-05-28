"""Tests for snapshot ⇄ xlsx/csv conversion (lazyclaw/sheets/xlsx_io.py)."""

from __future__ import annotations

from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets.xlsx_io import (
    snapshot_to_csv,
    snapshot_to_xlsx,
    xlsx_to_snapshot,
)


def _budget():
    snap = S.blank_workbook("Budget", workbook_id="wb", sheet_id="sh", sheet_name="Sheet1")
    return S.set_cells(snap, [
        {"row": 0, "col": 0, "value": 10},
        {"row": 1, "col": 0, "value": 20},
        {"row": 2, "col": 0, "formula": "=SUM(A1:A2)"},
        {"row": 0, "col": 1, "value": "hello"},
    ])


def test_xlsx_roundtrip_preserves_values_and_formulas():
    xb = snapshot_to_xlsx(_budget())
    assert isinstance(xb, bytes) and len(xb) > 0

    back = xlsx_to_snapshot(xb, name="Budget")
    assert S.sheet_names(back) == ["Sheet1"]
    assert S.cell_display(S.get_cell(back, 0, 0)) == 10
    assert S.cell_display(S.get_cell(back, 0, 1)) == "hello"
    # formula text survives the round-trip (data_only=False)
    assert S.get_cell(back, 2, 0)["f"] == "=SUM(A1:A2)"


def test_xlsx_formula_gets_leading_equals_on_export():
    snap = S.set_cell(
        S.blank_workbook("T", workbook_id="wb", sheet_id="sh"),
        0, 0, formula="A1+1",
    )
    # snapshot already normalises to "=A1+1"; ensure it survives to xlsx + back
    back = xlsx_to_snapshot(snapshot_to_xlsx(snap))
    assert S.get_cell(back, 0, 0)["f"].startswith("=")


def test_multi_sheet_roundtrip():
    snap = S.blank_workbook("Multi", workbook_id="wb", sheet_id="sh1", sheet_name="First")
    # add a second worksheet manually
    snap["sheets"]["sh2"] = {
        "id": "sh2", "name": "Second", "rowCount": 1000, "columnCount": 20,
        "cellData": {"0": {"0": {"v": 42}}},
    }
    snap["sheetOrder"].append("sh2")
    snap = S.set_cell(snap, 0, 0, value=1, sheet=0)

    back = xlsx_to_snapshot(snapshot_to_xlsx(snap))
    assert S.sheet_names(back) == ["First", "Second"]
    assert S.cell_display(S.get_cell(back, 0, 0, sheet="Second")) == 42


def test_empty_snapshot_exports_and_reimports():
    blank = S.blank_workbook("Empty", workbook_id="wb", sheet_id="sh")
    back = xlsx_to_snapshot(snapshot_to_xlsx(blank))
    assert back["sheetOrder"]  # at least one sheet


def test_csv_export_uses_display_grid():
    csv_text = snapshot_to_csv(_budget())
    lines = csv_text.strip().splitlines()
    # grid is dense to 2 cols because B1 has "hello"
    assert lines[0] == "10,hello"
    assert lines[1] == "20,"
    # no cached value on the formula cell → shows the formula text (+ empty B)
    assert lines[2] == "=SUM(A1:A2),"
