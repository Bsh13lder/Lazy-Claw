"""The agent-facing style model → Univer ``IStyleData``.

The encoding table below IS the contract with the web editor (`@univerjs/core`
0.24) and the Flutter grid (`mobile/lib/screens/documents/univer_model.dart`),
both of which read these keys directly. Get a key wrong and the formatting is
written but nothing renders it — so the encoding is tested field by field,
exactly, rather than round-tripped through our own reader.
"""

from __future__ import annotations

import pytest

from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets import styles as ST


def _wb():
    return S.blank_workbook("T", workbook_id="wb", sheet_id="sh")


def _cell(snap, row=0, col=0):
    return snap["sheets"]["sh"]["cellData"][str(row)][str(col)]


def _style_of(snap, row=0, col=0):
    """The resolved style dict behind a cell's ``s`` (id or inline)."""
    s = _cell(snap, row, col).get("s")
    return snap["styles"][s] if isinstance(s, str) else (s or {})


# ───────────────────── exact Univer key encoding ────────────────────

@pytest.mark.parametrize(
    "friendly,expected",
    [
        ({"bold": True}, {"bl": 1}),
        ({"italic": True}, {"it": 1}),
        ({"underline": True}, {"ul": {"s": 1}}),
        ({"strike": True}, {"st": {"s": 1}}),
        ({"color": "#FF0000"}, {"cl": {"rgb": "#FF0000"}}),
        ({"bg": "#00FF00"}, {"bg": {"rgb": "#00FF00"}}),
        ({"align": "left"}, {"ht": 1}),
        ({"align": "center"}, {"ht": 2}),
        ({"align": "right"}, {"ht": 3}),
        ({"valign": "top"}, {"vt": 1}),
        ({"valign": "middle"}, {"vt": 2}),
        ({"valign": "bottom"}, {"vt": 3}),
        ({"wrap": True}, {"tb": 3}),
        ({"number_format": "#,##0.00"}, {"n": {"pattern": "#,##0.00"}}),
        ({"font_size": 14}, {"fs": 14}),
        ({"font": "Arial"}, {"ff": "Arial"}),
    ],
)
def test_to_univer_style_exact_key_encoding(friendly, expected):
    assert ST.to_univer_style(friendly) == expected


def test_named_colors_resolve():
    assert ST.to_univer_style({"bg": "yellow"}) == {"bg": {"rgb": "#FFFF00"}}
    assert ST.to_univer_style({"color": "red"}) == {"cl": {"rgb": "#FF0000"}}


def test_color_normalisation_accepts_bare_and_short_hex():
    assert ST.normalize_color("ff0000") == "#FF0000"
    assert ST.normalize_color("#f00") == "#FF0000"
    assert ST.normalize_color("#FfAa00") == "#FFAA00"


def test_number_format_aliases():
    assert ST.to_univer_style({"number_format": "currency"})["n"]["pattern"] \
        == ST.NUMBER_FORMATS["currency"]
    assert ST.to_univer_style({"number_format": "percent"})["n"]["pattern"] == "0%"


def test_font_size_is_clamped():
    assert ST.to_univer_style({"font_size": 999})["fs"] == ST.FONT_SIZE_MAX
    assert ST.to_univer_style({"font_size": 1})["fs"] == ST.FONT_SIZE_MIN


def test_unknown_friendly_keys_are_ignored():
    assert ST.to_univer_style({"bold": True, "sparkle": "yes"}) == {"bl": 1}


# ───────────────────── falsy handling (the tb divergence) ───────────

@pytest.mark.parametrize("field,key", [
    ("bold", "bl"), ("italic", "it"), ("underline", "ul"), ("strike", "st"),
])
def test_false_removes_the_flag_key(field, key):
    """No inherited default for these, so a removal is enough to turn them off."""
    assert ST.to_univer_style({field: False}) == {key: None}


def test_wrap_false_writes_overflow_not_a_removal():
    """The web editor injects a workbook-level ``defaultStyle: {tb: WRAP}``.

    Removing ``tb`` would leave the cell inheriting WRAP from that cascade — so
    "wrap off" has to state OVERFLOW explicitly. (This is the one place we
    deliberately diverge from the Flutter `_mergePatch`, which removes on falsy.)
    """
    assert ST.to_univer_style({"wrap": False}) == {"tb": 1}


def test_explicit_none_removes_any_field():
    assert ST.to_univer_style({"bg": None}) == {"bg": None}
    assert ST.to_univer_style({"wrap": None}) == {"tb": None}


# ───────────────────── merge semantics ──────────────────────────────

def test_merge_keeps_unspecified_fields():
    base = {"bl": 1, "bg": {"rgb": "#FFFF00"}}
    out = ST.merge_style(base, ST.to_univer_style({"italic": True}))
    assert out == {"bl": 1, "bg": {"rgb": "#FFFF00"}, "it": 1}


def test_merge_removes_on_none_and_leaves_the_rest():
    base = {"bl": 1, "bg": {"rgb": "#FFFF00"}}
    out = ST.merge_style(base, ST.to_univer_style({"bold": False}))
    assert out == {"bg": {"rgb": "#FFFF00"}}


def test_merge_does_not_mutate_the_base():
    base = {"bl": 1}
    ST.merge_style(base, {"it": 1})
    assert base == {"bl": 1}


# ───────────────────── ids: deterministic + deduping ────────────────

def test_style_id_is_deterministic_and_key_order_independent():
    a = ST.style_id({"bl": 1, "bg": {"rgb": "#FFFF00"}})
    b = ST.style_id({"bg": {"rgb": "#FFFF00"}, "bl": 1})
    assert a == b == ST.style_id({"bl": 1, "bg": {"rgb": "#FFFF00"}})
    assert a.startswith(ST.STYLE_ID_PREFIX)


def test_style_id_treats_integral_floats_as_ints():
    """Otherwise `{"fs": 14}` and `{"fs": 14.0}` would dedup to two entries."""
    assert ST.style_id({"fs": 14}) == ST.style_id({"fs": 14.0})


def test_style_id_differs_for_different_styles():
    assert ST.style_id({"bl": 1}) != ST.style_id({"it": 1})


def test_apply_style_interns_once_and_shares_the_id():
    wb = ST.apply_style(_wb(), 0, 0, 0, 0, {"bold": True})
    wb = ST.apply_style(wb, 5, 5, 5, 5, {"bold": True})
    assert len(wb["styles"]) == 1
    assert _cell(wb, 0, 0)["s"] == _cell(wb, 5, 5)["s"]


def test_apply_style_is_idempotent():
    once = ST.apply_style(_wb(), 0, 0, 0, 1, {"bold": True})
    twice = ST.apply_style(once, 0, 0, 0, 1, {"bold": True})
    assert twice == once


# ───────────────────── the registry is never mutated ────────────────

def test_patching_one_cell_does_not_disturb_others_sharing_a_style():
    """A Univer-minted id can back many cells — patching one must mint a NEW id."""
    wb = _wb()
    wb["styles"] = {"1": {"bl": 1}}
    wb["sheets"]["sh"]["cellData"] = {
        "0": {"0": {"v": "a", "s": "1"}, "1": {"v": "b", "s": "1"}},
    }
    out = ST.apply_style(wb, 0, 0, 0, 0, {"bg": "yellow"})

    assert out["styles"]["1"] == {"bl": 1}, "shared registry entry was mutated"
    assert _cell(out, 0, 1)["s"] == "1", "the sibling cell was re-pointed"
    assert _cell(out, 0, 0)["s"] != "1"
    assert _style_of(out, 0, 0) == {"bl": 1, "bg": {"rgb": "#FFFF00"}}


def test_apply_style_accepts_an_inline_style_dict_as_the_base():
    """``ICellData.s`` is ``IStyleData | string`` — inline is legal."""
    wb = _wb()
    wb["sheets"]["sh"]["cellData"] = {"0": {"0": {"v": 1, "s": {"bl": 1}}}}
    out = ST.apply_style(wb, 0, 0, 0, 0, {"italic": True})
    assert _style_of(out, 0, 0) == {"bl": 1, "it": 1}


def test_apply_style_tolerates_a_null_registry_entry():
    wb = _wb()
    wb["styles"] = {"1": None}
    wb["sheets"]["sh"]["cellData"] = {"0": {"0": {"v": 1, "s": "1"}}}
    out = ST.apply_style(wb, 0, 0, 0, 0, {"bold": True})
    assert _style_of(out, 0, 0) == {"bl": 1}


# ───────────────────── ranges, clearing, immutability ───────────────

def test_apply_style_a1_covers_the_whole_range():
    wb = ST.apply_style_a1(_wb(), "A1:C1", {"bold": True})
    for col in range(3):
        assert _style_of(wb, 0, col) == {"bl": 1}
    assert "3" not in wb["sheets"]["sh"]["cellData"]["0"]


def test_apply_style_does_not_mutate_the_source():
    wb = _wb()
    ST.apply_style(wb, 0, 0, 0, 0, {"bold": True})
    assert wb["sheets"]["sh"]["cellData"] == {}
    assert wb["styles"] == {}


def test_apply_style_preserves_the_cell_value():
    wb = S.set_cell(_wb(), 0, 0, value=42)
    out = ST.apply_style(wb, 0, 0, 0, 0, {"bold": True})
    assert _cell(out, 0, 0)["v"] == 42


def test_clear_style_drops_s_and_leaves_the_value():
    wb = S.set_cell(_wb(), 0, 0, value=42)
    wb = ST.apply_style(wb, 0, 0, 0, 0, {"bold": True})
    out = ST.clear_style(wb, 0, 0, 0, 0)
    assert _cell(out, 0, 0) == {"v": 42}


def test_clear_style_removes_a_cell_that_was_style_only():
    wb = ST.apply_style(_wb(), 0, 0, 0, 0, {"bold": True})
    out = ST.clear_style(wb, 0, 0, 0, 0)
    assert out["sheets"]["sh"]["cellData"] == {}


# ───────────────────── garbage collection ───────────────────────────

def test_gc_drops_orphaned_lazyclaw_styles():
    wb = ST.apply_style(_wb(), 0, 0, 0, 0, {"bold": True})
    wb = ST.apply_style(wb, 0, 0, 0, 0, {"bold": True, "italic": True})
    assert len(wb["styles"]) == 1, "the superseded style should be swept"


def test_gc_never_drops_a_univer_minted_id():
    """Opaque plugin resources (CF, data validation, notes) may reference it."""
    wb = _wb()
    wb["styles"] = {"1": {"bl": 1}, "univer-x": {"it": 1}}
    out = ST.gc_styles(wb)
    assert set(out["styles"]) == {"1", "univer-x"}


def test_gc_keeps_styles_referenced_from_rows_columns_and_defaults():
    wb = _wb()
    wb["styles"] = {
        f"{ST.STYLE_ID_PREFIX}row": {"bl": 1},
        f"{ST.STYLE_ID_PREFIX}col": {"it": 1},
        f"{ST.STYLE_ID_PREFIX}dflt": {"tb": 3},
        f"{ST.STYLE_ID_PREFIX}orphan": {"st": {"s": 1}},
    }
    sheet = wb["sheets"]["sh"]
    sheet["rowData"] = {"0": {"s": f"{ST.STYLE_ID_PREFIX}row"}}
    sheet["columnData"] = {"0": {"s": f"{ST.STYLE_ID_PREFIX}col"}}
    wb["defaultStyle"] = f"{ST.STYLE_ID_PREFIX}dflt"

    out = ST.gc_styles(wb)
    assert f"{ST.STYLE_ID_PREFIX}orphan" not in out["styles"]
    assert len(out["styles"]) == 3


# ───────────────────── reading back ─────────────────────────────────

def test_resolve_and_view_round_trip():
    friendly = {
        "bold": True, "italic": True, "color": "#112233", "bg": "#AABBCC",
        "align": "center", "wrap": True, "number_format": "0.00",
        "font_size": 12,
    }
    wb = ST.apply_style(_wb(), 0, 0, 0, 0, friendly)
    view = ST.get_style_view(wb, 0, 0)
    for key, value in friendly.items():
        assert view[key] == (
            ST.normalize_color(value) if key in ("color", "bg") else value
        ), key


def test_style_view_of_an_unstyled_cell_is_empty():
    assert ST.get_style_view(_wb(), 0, 0) == {}


# ───────────────────── number formatting ────────────────────────────

@pytest.mark.parametrize(
    "value,pattern,expected",
    [
        (1234.5678, "#,##0.00", "1,234.57"),
        (1234.5, "$#,##0.00", "$1,234.50"),
        (0.5, "0%", "50%"),
        (3.7, "0", "4"),
        (3.14159, "0.00", "3.14"),
        ("text", "0.00", "text"),
        (None, "0.00", ""),
        (5, None, "5"),
    ],
)
def test_format_number(value, pattern, expected):
    assert ST.format_number(value, pattern) == expected
