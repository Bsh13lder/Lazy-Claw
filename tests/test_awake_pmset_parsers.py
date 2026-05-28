"""Unit tests for pmset output parsers in awake_bridge_server.py."""
from __future__ import annotations

import sys
import types

import pytest

# The bridge server is a standalone script with no lazyclaw imports.
# Import it directly without triggering the FastAPI/uvicorn dependency at
# module level (only _build_app() does that import).
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "awake_bridge_server",
    Path(__file__).parent.parent / "scripts" / "awake_bridge_server.py",
)
_mod = importlib.util.module_from_spec(_spec)
# Stub out sys/os exit calls if the module tries to call sys.exit at import.
# (It doesn't — but be safe.)
_spec.loader.exec_module(_mod)

parse_batt = _mod.parse_batt
parse_sched = _mod.parse_sched
_extract_clock = _mod._extract_clock


# ── parse_batt ────────────────────────────────────────────────────────────────

AC_BATT_OUTPUT = """\
Now drawing from 'AC Power'
 -InternalBattery-0 (id=1234567)	100%; charged; 0:00 remaining
"""

BATTERY_OUTPUT = """\
Now drawing from 'Battery Power'
 -InternalBattery-0 (id=1234567)	72%; discharging; 3:45 remaining
"""


def test_parse_batt_ac():
    r = parse_batt(AC_BATT_OUTPUT)
    assert r["on_ac_power"] is True
    assert r["battery_percent"] == 100


def test_parse_batt_battery():
    r = parse_batt(BATTERY_OUTPUT)
    assert r["on_ac_power"] is False
    assert r["battery_percent"] == 72


def test_parse_batt_empty():
    r = parse_batt("")
    assert r["on_ac_power"] is False
    assert r["battery_percent"] is None


# ── parse_sched ───────────────────────────────────────────────────────────────

SCHED_WITH_REPEAT = """\
Repeating power events:
 wakepoweron at 7:00AM every day

Scheduled power events:
 [0] wake at 05/29/2026 10:30:00
"""

SCHED_EMPTY = """\
Repeating power events:
 No repeating power events.

Scheduled power events:
 No scheduled power events.
"""

SCHED_NO_SECTION = "No scheduled power events.\n"


def test_parse_sched_with_repeat():
    r = parse_sched(SCHED_WITH_REPEAT)
    assert r["daily_wake"] == "07:00"
    assert len(r["scheduled_wakes"]) == 1


def test_parse_sched_empty():
    r = parse_sched(SCHED_EMPTY)
    assert r["daily_wake"] is None
    assert r["scheduled_wakes"] == []


def test_parse_sched_no_section():
    r = parse_sched(SCHED_NO_SECTION)
    assert r["daily_wake"] is None


# ── _extract_clock ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("wakepoweron at 7:00AM every day", "07:00"),
    ("wakepoweron at 12:00PM every day", "12:00"),
    ("wake at 23:59", "23:59"),
    ("wake at 12:00AM", "00:00"),
    ("wake at 11:30PM", "23:30"),
])
def test_extract_clock(line, expected):
    assert _extract_clock(line) == expected


def test_extract_clock_no_match():
    assert _extract_clock("No time here") == ""
