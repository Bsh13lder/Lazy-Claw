"""Tests for best-effort server-side recalc (lazyclaw/sheets/recalc.py)."""

from __future__ import annotations

from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets.recalc import recalc


def _wb():
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh", sheet_name="Sheet1")


def test_recalc_sum():
    snap = S.set_cells(_wb(), [
        {"row": 0, "col": 0, "value": 10},
        {"row": 1, "col": 0, "value": 20},
        {"row": 2, "col": 0, "formula": "=SUM(A1:A2)"},
    ])
    out = recalc(snap)
    assert S.get_cell(out, 2, 0)["v"] == 30
    # formula text retained alongside the computed value
    assert S.get_cell(out, 2, 0)["f"] == "=SUM(A1:A2)"


def test_recalc_arithmetic():
    snap = S.set_cells(_wb(), [
        {"row": 0, "col": 0, "value": 6},
        {"row": 0, "col": 1, "value": 7},
        {"row": 0, "col": 2, "formula": "=A1*B1"},
    ])
    out = recalc(snap)
    assert S.get_cell(out, 0, 2)["v"] == 42


def test_recalc_is_immutable():
    snap = S.set_cells(_wb(), [
        {"row": 0, "col": 0, "value": 1},
        {"row": 1, "col": 0, "formula": "=A1+1"},
    ])
    out = recalc(snap)
    # original formula cell still has no cached value
    assert "v" not in snap["sheets"]["sh"]["cellData"]["1"]["0"]
    assert out is not snap


def test_recalc_no_formulas_is_noop_copy():
    snap = S.set_cell(_wb(), 0, 0, value=5)
    out = recalc(snap)
    assert out == snap
    assert out is not snap


def test_recalc_never_raises_on_garbage_formula():
    snap = S.set_cell(_wb(), 0, 0, formula="=THIS_IS_NOT_A_FUNC(99)")
    # must not raise; the cell simply keeps whatever it had (no v)
    out = recalc(snap)
    assert "f" in S.get_cell(out, 0, 0)
