"""A value/formula write must not destroy the cell's FORMATTING.

Regression suite for a live data-loss bug: ``_apply_cell`` rebuilt every cell
dict from scratch, so any style the user applied in the web editor (Univer
stores it as ``ICellData.s`` — an id into the workbook ``styles`` registry, or
an inline style dict) was silently wiped the moment an agent skill touched that
cell. The web editor persists the snapshot verbatim, so the damage was
permanent.

The rule these tests pin down:

    preserve ``s`` and ``custom``; drop everything value-shaped
    (``v f t p si ref xf``).

Dropping the value-shaped keys is deliberate, not laziness:

* ``p`` (rich text ``IDocumentData``) *shadows* ``v`` in Univer's renderer, so
  keeping it would make the new value invisible — strictly worse than the bug
  we're fixing.
* ``t`` (``CellValueType``) would go stale and mislabel the new value; dropping
  it lets Univer re-infer.
* ``si`` / ``ref`` / ``xf`` all describe the formula being replaced.
"""

from __future__ import annotations

from lazyclaw.sheets import snapshot as S


def _wb():
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh")


def _with_cell(cell: dict) -> dict:
    """A workbook whose A1 is exactly ``cell`` (bypasses set_cell on purpose)."""
    wb = _wb()
    wb["sheets"]["sh"]["cellData"] = {"0": {"0": dict(cell)}}
    return wb


def _a1(snap: dict) -> dict:
    return snap["sheets"]["sh"]["cellData"]["0"]["0"]


# ───────────────────────── style survives a write ────────────────────

def test_set_cell_preserves_existing_style_id():
    wb = _with_cell({"v": 1, "s": "s-x"})
    out = S.set_cell(wb, 0, 0, value=2)
    assert _a1(out) == {"v": 2, "s": "s-x"}


def test_set_cell_preserves_inline_style_dict():
    """``ICellData.s`` is ``IStyleData | string`` — an inline dict is legal."""
    wb = _with_cell({"v": 1, "s": {"bl": 1, "bg": {"rgb": "#FFFF00"}}})
    out = S.set_cell(wb, 0, 0, value="hello")
    assert _a1(out)["s"] == {"bl": 1, "bg": {"rgb": "#FFFF00"}}


def test_set_cell_preserves_custom():
    """``custom`` is plugin/user data, orthogonal to the value."""
    wb = _with_cell({"v": 1, "custom": {"note": "keep me"}})
    out = S.set_cell(wb, 0, 0, value=2)
    assert _a1(out)["custom"] == {"note": "keep me"}


def test_set_cells_batch_preserves_style_on_every_cell():
    wb = _wb()
    wb["sheets"]["sh"]["cellData"] = {
        "0": {"0": {"v": 1, "s": "s-a"}, "1": {"v": 2, "s": "s-b"}},
        "1": {"0": {"v": 3, "s": "s-c"}},
    }
    out = S.set_cells(
        wb,
        [
            {"row": 0, "col": 0, "value": 10},
            {"row": 0, "col": 1, "value": 20},
            {"row": 1, "col": 0, "value": 30},
        ],
    )
    cd = out["sheets"]["sh"]["cellData"]
    assert cd["0"]["0"] == {"v": 10, "s": "s-a"}
    assert cd["0"]["1"] == {"v": 20, "s": "s-b"}
    assert cd["1"]["0"] == {"v": 30, "s": "s-c"}


def test_formula_write_preserves_style():
    wb = _with_cell({"v": 5, "s": "s-x"})
    out = S.set_cell(wb, 0, 0, formula="=SUM(B1:B9)")
    assert _a1(out) == {"f": "=SUM(B1:B9)", "s": "s-x"}


def test_a1_edit_form_preserves_style():
    wb = _with_cell({"v": 1, "s": "s-x"})
    out = S.set_cells(wb, [{"cell": "A1", "value": 7}])
    assert _a1(out) == {"v": 7, "s": "s-x"}


# ───────────────────────── stale value-shaped keys go ────────────────

def test_formula_write_drops_stale_shared_and_array_formula_keys():
    """``si``/``ref``/``xf`` describe the formula being replaced."""
    wb = _with_cell(
        {"f": "=A1", "si": "grp1", "ref": "A1:A3", "xf": "_xlfn.", "s": "s-x"}
    )
    out = S.set_cell(wb, 0, 0, formula="=B1")
    assert _a1(out) == {"f": "=B1", "s": "s-x"}


def test_value_write_drops_rich_text_p():
    """``p`` shadows ``v`` in Univer's renderer — keeping it hides the write."""
    wb = _with_cell({"p": {"body": {"dataStream": "old\r\n"}}, "s": "s-x"})
    out = S.set_cell(wb, 0, 0, value="new")
    assert "p" not in _a1(out)
    assert _a1(out) == {"v": "new", "s": "s-x"}


def test_value_write_drops_stale_cell_type():
    """A stale numeric ``t`` would mislabel a string we just wrote."""
    wb = _with_cell({"v": 42, "t": 2, "s": "s-x"})
    out = S.set_cell(wb, 0, 0, value="forty two")
    assert "t" not in _a1(out)


def test_value_write_replaces_previous_formula():
    wb = _with_cell({"f": "=SUM(A2:A9)", "v": 99, "s": "s-x"})
    out = S.set_cell(wb, 0, 0, value=5)
    assert _a1(out) == {"v": 5, "s": "s-x"}


# ───────────────────────── documented asymmetry ──────────────────────

def test_explicit_clear_removes_the_style_too():
    """Deliberate divergence from Excel's Delete key, pinned so it stays so.

    ``set_cell(snap, r, c)`` with no value and no formula drops the whole cell
    — style included. Excel's Delete clears contents and keeps the format.
    Changing this would silently alter semantics for every existing caller, so
    the behaviour stays and this test documents it.
    """
    wb = _with_cell({"v": 1, "s": "s-x"})
    out = S.set_cell(wb, 0, 0)
    assert out["sheets"]["sh"]["cellData"] == {}


# ───────────────────────── immutability still holds ──────────────────

def test_preserving_style_does_not_alias_the_source_cell():
    """The new snapshot must not share the style object with the original."""
    wb = _with_cell({"v": 1, "s": {"bl": 1}})
    out = S.set_cell(wb, 0, 0, value=2)
    out["sheets"]["sh"]["cellData"]["0"]["0"]["s"]["bl"] = 0
    assert wb["sheets"]["sh"]["cellData"]["0"]["0"]["s"] == {"bl": 1}


def test_write_to_empty_cell_is_unchanged_by_the_fix():
    out = S.set_cell(_wb(), 0, 0, value=10)
    assert _a1(out) == {"v": 10}
