"""Low-stock threshold evaluation + instruction composition."""
from __future__ import annotations

import pytest

from lazyclaw.tasks.stock_watch import (
    build_stock_watch_instruction,
    evaluate_threshold,
)


def test_threshold_trips_on_low_rows():
    rows = [{"sku": "a", "qty": 2}, {"sku": "b", "qty": 10}, {"sku": "c", "qty": 0}]
    result = evaluate_threshold(rows, "qty", "<", 5)
    assert result.tripped is True
    assert [r["sku"] for r in result.breaching] == ["a", "c"]
    assert "2 item" in result.summary


def test_threshold_quiet_when_all_ok():
    rows = [{"sku": "a", "qty": 8}, {"sku": "b", "qty": 10}]
    result = evaluate_threshold(rows, "qty", "<", 5)
    assert result.tripped is False
    assert result.breaching == []


def test_threshold_skips_missing_and_nonnumeric():
    rows = [{"sku": "a"}, {"sku": "b", "qty": "lots"}, {"sku": "c", "qty": True},
            {"sku": "d", "qty": 1}]
    result = evaluate_threshold(rows, "qty", "<", 5)
    # Only the genuine numeric low row (d) counts; bool True is not treated as 1.
    assert [r["sku"] for r in result.breaching] == ["d"]


def test_threshold_operators():
    rows = [{"n": 5}]
    assert evaluate_threshold(rows, "n", "<=", 5).tripped is True
    assert evaluate_threshold(rows, "n", ">=", 5).tripped is True
    assert evaluate_threshold(rows, "n", "==", 5).tripped is True
    assert evaluate_threshold(rows, "n", "<", 5).tripped is False


def test_unknown_operator_raises():
    with pytest.raises(ValueError):
        evaluate_threshold([{"n": 1}], "n", "=<", 5)


def test_instruction_alert_only():
    text = build_stock_watch_instruction(
        how_to_check="call panel_call with site='shop', name='low_stock'",
        low_condition="quantity below 5",
    )
    assert "panel_call" in text
    assert "stay silent" in text.lower()
    assert "do not place any order" in text.lower()


def test_instruction_with_gated_restock():
    text = build_stock_watch_instruction(
        how_to_check="run the db-toolbox 'low_stock' query",
        low_condition="quantity below 5",
        restock="panel_call site='shop' name='reorder'",
    )
    assert "explicit approval" in text.lower()
    assert "confirm=true" in text
