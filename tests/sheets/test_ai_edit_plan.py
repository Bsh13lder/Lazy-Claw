"""Validation of the ✨ edit plan an LLM returns.

Pure — no DB fixture — so the nastiest shapes are cheap to enumerate. The
guiding rule everywhere: a bad entry is SKIPPED, never raised, and never sinks
its good siblings. A model that gets one of six formats wrong should still get
the other five applied.
"""

from __future__ import annotations

import pytest

from lazyclaw.sheets import ai_edit_plan as P


# ───────────────────────── edits (unchanged contract) ───────────────

def test_normalize_edits_accepts_both_address_forms():
    assert P.normalize_edits([
        {"cell": "A1", "value": 10},
        {"row": 1, "col": 2, "formula": "=SUM(A1:A2)"},
    ]) == [
        {"cell": "A1", "value": 10},
        {"row": 1, "col": 2, "formula": "=SUM(A1:A2)"},
    ]


@pytest.mark.parametrize("raw", [None, "nope", 42, {}, [], [1, "x", None]])
def test_normalize_edits_shrugs_off_garbage(raw):
    assert P.normalize_edits(raw) == []


def test_normalize_edits_drops_an_addressless_entry():
    assert P.normalize_edits([{"value": 1}, {"cell": "A1", "value": 2}]) == [
        {"cell": "A1", "value": 2}
    ]


# ───────────────────────── formats ──────────────────────────────────

def test_normalize_formats_keeps_a_good_entry():
    assert P.normalize_formats([{"range": "A1:C1", "bold": True}]) == [
        {"range": "A1:C1", "bold": True}
    ]


def test_normalize_formats_accepts_cell_as_an_alias_for_range():
    assert P.normalize_formats([{"cell": "B2", "italic": True}]) == [
        {"range": "B2", "italic": True}
    ]


def test_a_bad_range_drops_only_that_entry():
    out = P.normalize_formats([
        {"range": "!!!", "bold": True},
        {"range": "A1", "bold": True},
    ])
    assert out == [{"range": "A1", "bold": True}]


def test_an_entry_without_a_range_is_dropped():
    assert P.normalize_formats([{"bold": True}]) == []


def test_unknown_fields_are_stripped_but_the_entry_survives():
    assert P.normalize_formats([
        {"range": "A1", "bold": True, "sparkle": "yes", "rainbow": 3}
    ]) == [{"range": "A1", "bold": True}]


def test_an_entry_with_nothing_left_to_apply_is_dropped():
    assert P.normalize_formats([{"range": "A1", "sparkle": "yes"}]) == []


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("false", False), ("False", False),
])
def test_stringly_booleans_are_coerced(raw, expected):
    """Models emit these constantly; refusing them wastes a whole retry."""
    assert P.normalize_formats([{"range": "A1", "bold": raw}]) == [
        {"range": "A1", "bold": expected}
    ]


def test_a_nonsense_boolean_drops_the_key_not_the_entry():
    assert P.normalize_formats([
        {"range": "A1", "bold": "maybe", "italic": True}
    ]) == [{"range": "A1", "italic": True}]


def test_an_unparseable_colour_drops_the_key_not_the_entry():
    assert P.normalize_formats([
        {"range": "A1", "bg": "chartreuse-ish", "bold": True}
    ]) == [{"range": "A1", "bold": True}]


def test_alignment_is_lowercased_and_enum_checked():
    assert P.normalize_formats([{"range": "A1", "align": "CENTER"}]) == [
        {"range": "A1", "align": "center"}
    ]
    assert P.normalize_formats([{"range": "A1", "align": "diagonal"}]) == []


def test_font_size_is_coerced_and_clamped():
    assert P.normalize_formats([{"range": "A1", "font_size": "14"}]) == [
        {"range": "A1", "font_size": 14.0}
    ]
    assert P.normalize_formats([{"range": "A1", "font_size": 500}])[0][
        "font_size"
    ] <= 72


def test_clear_survives_on_its_own():
    assert P.normalize_formats([{"range": "A1:C3", "clear": True}]) == [
        {"range": "A1:C3", "clear": True}
    ]


def test_formats_are_capped():
    raw = [{"range": "A1", "bold": True}] * (P.MAX_FORMAT_OPS + 20)
    assert len(P.normalize_formats(raw)) == P.MAX_FORMAT_OPS


@pytest.mark.parametrize("raw", [None, "x", 7, {}, [None, 3]])
def test_normalize_formats_shrugs_off_garbage(raw):
    assert P.normalize_formats(raw) == []


# ───────────────────────── layout ───────────────────────────────────

def test_layout_column_widths_letters_become_indices():
    assert P.normalize_layout({
        "column_widths": [{"column": "B", "width": 160}]
    })["column_widths"] == {1: 160.0}


def test_layout_row_heights_are_converted_from_1_based():
    assert P.normalize_layout({
        "row_heights": [{"row": 1, "height": 32}]
    })["row_heights"] == {0: 32.0}


def test_layout_row_zero_is_dropped_not_wrapped_to_negative():
    assert "row_heights" not in P.normalize_layout({
        "row_heights": [{"row": 0, "height": 32}]
    })


def test_layout_widths_are_clamped():
    out = P.normalize_layout({"column_widths": [{"column": "A", "width": 99999}]})
    assert out["column_widths"][0] <= 2000


def test_layout_autofit_star_is_preserved():
    assert P.normalize_layout({"auto_fit_columns": ["*"]})["auto_fit_columns"] \
        == P.AUTOFIT_ALL


def test_layout_autofit_letters_become_indices():
    assert P.normalize_layout({
        "auto_fit_columns": ["A", "C", "zzz-nope"]
    })["auto_fit_columns"] == [0, 2]


def test_layout_merge_ranges_are_validated():
    out = P.normalize_layout({"merge": ["A1:C1", "!!!"]})
    assert out["merge"] == ["A1:C1"]


def test_layout_freeze_is_clamped_and_absent_stays_absent():
    out = P.normalize_layout({"freeze_rows": 900})
    assert out["freeze_rows"] == 100
    assert "freeze_columns" not in out, "absent must differ from 0"


def test_layout_freeze_zero_is_preserved():
    """0 means 'unfreeze' — distinct from 'don't touch the freeze'."""
    assert P.normalize_layout({"freeze_rows": 0})["freeze_rows"] == 0


@pytest.mark.parametrize("raw", [None, "x", 7, [], {}, {"nonsense": 1}])
def test_normalize_layout_shrugs_off_garbage(raw):
    assert P.normalize_layout(raw) == {}


def test_layout_ops_are_capped():
    raw = {"merge": ["A1:B1"] * (P.MAX_LAYOUT_OPS + 20)}
    assert len(P.normalize_layout(raw)["merge"]) == P.MAX_LAYOUT_OPS
