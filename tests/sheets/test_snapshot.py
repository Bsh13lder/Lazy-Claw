"""Unit tests for the pure Univer-snapshot helpers (lazyclaw/sheets/snapshot.py).

These functions carry the whole cell model, so they're tested hard: A1
conversion edge cases, numeric coercion, immutability of every mutator, batch
edits, sheet resolution by index/name/id, and the dense read grid.
"""

from __future__ import annotations

import pytest

from lazyclaw.sheets import snapshot as S


# ───────────────────────── A1 ⇄ (row, col) ──────────────────────────

@pytest.mark.parametrize(
    "col,letter",
    [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (701, "ZZ"), (702, "AAA")],
)
def test_col_letter_roundtrip(col, letter):
    assert S.col_to_letter(col) == letter
    assert S.letter_to_col(letter) == col


def test_col_to_letter_rejects_negative():
    with pytest.raises(ValueError):
        S.col_to_letter(-1)


@pytest.mark.parametrize("ref,rc", [("A1", (0, 0)), ("B3", (2, 1)), ("Z10", (9, 25)), ("AA1", (0, 26))])
def test_a1_to_rc(ref, rc):
    assert S.a1_to_rc(ref) == rc


def test_a1_handles_absolute_refs():
    assert S.a1_to_rc("$B$3") == (2, 1)


def test_rc_to_a1_roundtrip():
    for row in (0, 5, 99):
        for col in (0, 1, 26, 51):
            assert S.a1_to_rc(S.rc_to_a1(row, col)) == (row, col)


def test_a1_to_rc_rejects_garbage():
    with pytest.raises(ValueError):
        S.a1_to_rc("not-a-ref")


# ───────────────────────── value coercion ───────────────────────────

def test_coerce_value_numbers_and_strings():
    assert S.coerce_value("10") == 10 and isinstance(S.coerce_value("10"), int)
    assert S.coerce_value("3.5") == 3.5
    assert S.coerce_value("hello") == "hello"
    assert S.coerce_value("") == ""
    assert S.coerce_value(42) == 42
    assert S.coerce_value(None) is None


def test_coerce_value_keeps_bool_not_int():
    # bool is an int subclass — must not be coerced to 0/1.
    assert S.coerce_value(True) is True


# ───────────────────────── workbook factory ─────────────────────────

def test_blank_workbook_is_valid_and_empty():
    wb = S.blank_workbook("Budget", workbook_id="wb-x", sheet_id="sh-x")
    assert wb["id"] == "wb-x"
    assert wb["name"] == "Budget"
    assert wb["sheetOrder"] == ["sh-x"]
    assert wb["sheets"]["sh-x"]["cellData"] == {}
    assert wb["sheets"]["sh-x"]["rowCount"] == S.DEFAULT_ROWS
    assert S.sheet_names(wb) == ["Sheet1"]


def test_blank_workbook_defaults_name():
    assert S.blank_workbook("   ")["name"] == "Untitled sheet"


# ───────────────────────── set_cell immutability ────────────────────

def _wb():
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh")


def test_set_cell_value_returns_new_snapshot():
    wb = _wb()
    out = S.set_cell(wb, 0, 0, value=10)
    # original untouched
    assert wb["sheets"]["sh"]["cellData"] == {}
    # new copy has the cell
    assert out["sheets"]["sh"]["cellData"]["0"]["0"] == {"v": 10}


def test_set_cell_coerces_numeric_string():
    out = S.set_cell(_wb(), 1, 1, value="20")
    assert out["sheets"]["sh"]["cellData"]["1"]["1"]["v"] == 20


def test_set_cell_formula_adds_leading_equals():
    out = S.set_cell(_wb(), 2, 0, formula="SUM(A1:A2)")
    assert out["sheets"]["sh"]["cellData"]["2"]["0"]["f"] == "=SUM(A1:A2)"


def test_set_cell_formula_keeps_existing_equals():
    out = S.set_cell(_wb(), 2, 0, formula="=A1*B1")
    assert out["sheets"]["sh"]["cellData"]["2"]["0"]["f"] == "=A1*B1"


def test_set_cell_clears_when_both_none():
    wb = S.set_cell(_wb(), 0, 0, value=5)
    cleared = S.set_cell(wb, 0, 0)
    assert cleared["sheets"]["sh"]["cellData"] == {}


def test_set_cell_zero_preserved():
    out = S.set_cell(_wb(), 0, 0, value=0)
    assert S.cell_display(S.get_cell(out, 0, 0)) == 0


# ───────────────────────── set_cells batch ──────────────────────────

def test_set_cells_batch_rowcol_and_a1():
    wb = _wb()
    out = S.set_cells(wb, [
        {"row": 0, "col": 0, "value": 10},
        {"row": 1, "col": 0, "value": 20},
        {"cell": "A3", "formula": "=SUM(A1:A2)"},
    ])
    assert wb["sheets"]["sh"]["cellData"] == {}  # immutable
    cd = out["sheets"]["sh"]["cellData"]
    assert cd["0"]["0"]["v"] == 10
    assert cd["1"]["0"]["v"] == 20
    assert cd["2"]["0"]["f"] == "=SUM(A1:A2)"


# ───────────────────────── read helpers ─────────────────────────────

def test_get_cell_none_when_empty():
    assert S.get_cell(_wb(), 5, 5) is None


def test_cell_display_prefers_value_then_formula():
    assert S.cell_display({"v": 30, "f": "=SUM(A1:A2)"}) == 30
    assert S.cell_display({"f": "=A1"}) == "=A1"
    assert S.cell_display(None) == ""


def test_iter_cells_is_row_major_sorted():
    wb = S.set_cells(_wb(), [
        {"row": 10, "col": 2, "value": "c"},
        {"row": 0, "col": 1, "value": "a"},
        {"row": 0, "col": 0, "value": "b"},
    ])
    coords = [(r, c) for r, c, _ in S.iter_cells(wb)]
    assert coords == [(0, 0), (0, 1), (10, 2)]


def test_used_bounds_and_as_grid():
    wb = S.set_cells(_wb(), [
        {"row": 0, "col": 0, "value": 1},
        {"row": 0, "col": 1, "value": 2},
        {"row": 2, "col": 0, "formula": "=A1+B1"},
    ])
    assert S.used_bounds(wb) == (2, 1)
    grid = S.as_grid(wb)
    assert grid == [[1, 2], ["", ""], ["=A1+B1", ""]]


def test_as_grid_empty_sheet():
    assert S.as_grid(_wb()) == []


# ───────────────────────── sheet resolution ─────────────────────────

def test_resolve_sheet_by_index_name_id():
    wb = S.blank_workbook("T", workbook_id="wb", sheet_id="sh", sheet_name="Data")
    assert S._resolve_sheet_id(wb, 0) == "sh"
    assert S._resolve_sheet_id(wb, "sh") == "sh"
    assert S._resolve_sheet_id(wb, "data") == "sh"  # case-insensitive name


def test_resolve_sheet_bad_selector_raises():
    with pytest.raises(KeyError):
        S._resolve_sheet_id(_wb(), "nonexistent")
