"""Range parsing + the content/formatting distinction.

Two primitives the formatting layer needs and the repo did not have:

* :func:`parse_range` — nothing here parsed ``"A1:C5"`` before. Formatting is
  range-shaped ("bold A1:D1"), so styles.py and geometry.py both need it, and
  it belongs next to ``a1_to_rc`` because it is an address concern.
* :func:`has_content` — a cell carrying ONLY a style (``{"s": "..."}``) is not
  content. Without this, formatting an empty header row would grow phantom rows
  in ``read_sheet``/``as_grid``/CSV export.
"""

from __future__ import annotations

import pytest

from lazyclaw.sheets import snapshot as S


def _wb():
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh")


# ───────────────────────── parse_range ──────────────────────────────

@pytest.mark.parametrize(
    "ref,expected",
    [
        ("A1", (0, 0, 0, 0)),          # degenerate single cell
        ("B3", (2, 1, 2, 1)),
        ("A1:C5", (0, 0, 4, 2)),
        ("a1:c5", (0, 0, 4, 2)),        # case-insensitive
        (" A1 : C5 ", (0, 0, 4, 2)),    # tolerant of whitespace
        ("$A$1:$C$5", (0, 0, 4, 2)),    # absolute markers ignored
        ("C5:A1", (0, 0, 4, 2)),        # reversed → normalised
        ("A5:C1", (0, 0, 4, 2)),        # mixed reversal → normalised
        ("AA10", (9, 26, 9, 26)),
    ],
)
def test_parse_range_forms(ref, expected):
    assert S.parse_range(ref) == expected


def test_parse_whole_column():
    """``A:A`` — end row is the caller's problem (clamp via used_bounds)."""
    r1, c1, r2, c2 = S.parse_range("B:D")
    assert (r1, c1, c2) == (0, 1, 3)
    assert r2 >= S.MAX_ROW_INDEX - 1


def test_parse_whole_row():
    r1, c1, r2, c2 = S.parse_range("2:4")
    assert (r1, r2, c1) == (1, 3, 0)
    assert c2 >= S.MAX_COL_INDEX - 1


@pytest.mark.parametrize(
    "bad", ["", "   ", "A", "1", "A1:", ":C5", "A1:B2:C3", "hello", "A0", "$$"]
)
def test_parse_range_rejects_garbage(bad):
    with pytest.raises(ValueError):
        S.parse_range(bad)


def test_parse_range_is_inclusive_of_both_ends():
    r1, c1, r2, c2 = S.parse_range("A1:B2")
    assert (r2 - r1 + 1, c2 - c1 + 1) == (2, 2)


# ───────────────────────── has_content ──────────────────────────────

@pytest.mark.parametrize(
    "cell,expected",
    [
        ({"v": 1}, True),
        ({"v": 0}, True),              # zero is content
        ({"v": ""}, True),             # an explicit empty string is content
        ({"f": "=A1"}, True),
        ({"s": "s-x"}, False),         # style only — NOT content
        ({"s": {"bl": 1}}, False),
        ({"custom": {"a": 1}}, False),
        ({}, False),
        (None, False),
        ({"v": None, "s": "s-x"}, False),
    ],
)
def test_has_content(cell, expected):
    assert S.has_content(cell) is expected


def test_style_only_cell_does_not_inflate_used_bounds():
    """Formatting a far-off empty cell must not grow the used range."""
    wb = _wb()
    wb["sheets"]["sh"]["cellData"] = {"9": {"3": {"s": "s-x"}}}
    assert S.used_bounds(wb) == (-1, -1)
    assert S.as_grid(wb) == []


def test_style_only_cells_do_not_pad_the_grid_around_real_content():
    wb = _wb()
    wb["sheets"]["sh"]["cellData"] = {
        "0": {"0": {"v": "hi"}},
        "9": {"3": {"s": "s-x"}},
    }
    assert S.used_bounds(wb) == (0, 0)
    assert S.as_grid(wb) == [["hi"]]


def test_iter_cells_still_yields_style_only_cells():
    """xlsx export walks cellData for styles — iter_cells must not filter."""
    wb = _wb()
    wb["sheets"]["sh"]["cellData"] = {"2": {"1": {"s": "s-x"}}}
    assert [(r, c) for r, c, _ in S.iter_cells(wb)] == [(2, 1)]


# ───────────────────────── public sheet resolver ────────────────────

def test_resolve_sheet_id_is_public():
    """styles.py / geometry.py need it without reaching into a private."""
    wb = _wb()
    assert S.resolve_sheet_id(wb, 0) == "sh"
    assert S.resolve_sheet_id(wb, "Sheet1") == "sh"
    assert S.resolve_sheet_id(wb, "sh") == "sh"
