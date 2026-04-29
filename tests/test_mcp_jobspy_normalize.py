"""Unit tests for mcp_jobspy.normalize.normalize_row.

Pure-function tests — no network, no jobspy install required.
Covers the three real-data bugs found in the live MCP output:

1. NaN salary slipped through `if min_sal and max_sal` and produced
   "$nan-$nan" because bool(NaN) is True in Python.
2. Float salaries were rendered "$50.0" instead of "$50".
3. NaN strings were coerced via str(NaN) → "nan" leaking into output.
"""

from __future__ import annotations

import math

import pytest

from mcp_jobspy.normalize import (
    _format_salary,
    _is_nan,
    _num,
    _text,
    normalize_row,
)

NAN = float("nan")


# ---- helpers --------------------------------------------------------------

def test_is_nan_only_for_nan_floats():
    assert _is_nan(NAN) is True
    assert _is_nan(0.0) is False
    assert _is_nan(None) is False
    assert _is_nan("nan") is False
    assert _is_nan(0) is False


def test_text_drops_none_and_nan():
    assert _text(None) == ""
    assert _text(NAN) == ""
    assert _text("  hi  ") == "hi"
    assert _text(42) == "42"


def test_num_drops_nan_and_none():
    assert _num(NAN) is None
    assert _num(None) is None
    assert _num("nope") is None
    assert _num(50.0) == 50.0
    assert _num("100") == 100.0


# ---- salary formatting ----------------------------------------------------

def test_format_salary_whole_floats_render_as_int():
    assert _format_salary(50.0, 65.0, "hourly", "USD") == "$50-$65/hourly"


def test_format_salary_fractional_keeps_two_decimals():
    assert _format_salary(50.5, 65.25, "", "USD") == "$50.50-$65.25"


def test_format_salary_min_only():
    assert _format_salary(100000.0, None, "yearly", "USD") == "$100000+/yearly"


def test_format_salary_max_only():
    assert _format_salary(None, 80000.0, "yearly", "USD") == "up to $80000/yearly"


def test_format_salary_equal_bounds_collapses():
    assert _format_salary(50.0, 50.0, "hourly", "USD") == "$50/hourly"


def test_format_salary_missing_returns_empty():
    assert _format_salary(None, None, "", "") == ""
    assert _format_salary(NAN, NAN, "", "") == ""  # via _num upstream this is impossible, but guard


def test_format_salary_currency_eur():
    assert _format_salary(40000.0, 60000.0, "yearly", "EUR") == "€40000-€60000/yearly"


def test_format_salary_currency_unknown_passes_through():
    assert _format_salary(100.0, 200.0, "", "JPY") == "JPY100-JPY200"


# ---- normalize_row --------------------------------------------------------

def _row(**overrides):
    """Sensible defaults for a single jobspy row dict."""
    base = {
        "id": "in-abc123",
        "site": "indeed",
        "job_url": "https://indeed.com/viewjob?jk=abc123",
        "job_url_direct": "",
        "title": "Python Engineer",
        "company": "Acme Co",
        "location": "Remote, US",
        "date_posted": "2026-04-25",
        "job_type": "fulltime",
        "interval": "yearly",
        "min_amount": 100000.0,
        "max_amount": 150000.0,
        "currency": "USD",
        "is_remote": True,
        "description": "Build cool things.",
    }
    base.update(overrides)
    return base


def test_normalize_row_happy_path():
    job = normalize_row(_row())
    assert job["title"] == "Python Engineer"
    assert job["company"] == "Acme Co"
    assert job["salary"] == "$100000-$150000/yearly"
    assert job["budget"] == "$100000-$150000/yearly"
    assert job["is_remote"] is True
    assert job["job_type"] == "fulltime"
    assert job["date_posted"] == "2026-04-25"


def test_normalize_row_nan_salary_does_not_emit_nan():
    """The bug that triggered this whole patch — must not produce $nan-$nan."""
    job = normalize_row(_row(min_amount=NAN, max_amount=NAN))
    assert "nan" not in job["salary"].lower()
    assert job["salary"] == ""
    assert job["budget"] == "N/A"


def test_normalize_row_partial_nan_salary():
    job = normalize_row(_row(min_amount=NAN, max_amount=80000.0))
    assert job["salary"] == "up to $80000/yearly"


def test_normalize_row_nan_strings_stripped_to_empty():
    """str(NaN) used to leak 'nan' into title/company/description."""
    job = normalize_row(_row(title=NAN, company=NAN, description=NAN, location=NAN))
    assert job["title"] == ""
    assert job["company"] == ""
    assert job["description"] == ""
    assert job["location"] == ""


def test_normalize_row_prefers_direct_url():
    job = normalize_row(_row(job_url_direct="https://acme.co/careers/123"))
    assert job["url"] == "https://acme.co/careers/123"


def test_normalize_row_falls_back_to_indirect_url():
    job = normalize_row(_row(job_url_direct=""))
    assert job["url"].startswith("https://indeed.com/")


def test_normalize_row_truncates_description():
    long_desc = "x" * 1000
    job = normalize_row(_row(description=long_desc))
    assert len(job["description"]) == 500


def test_normalize_row_skips_optional_fields_when_missing():
    job = normalize_row(_row(date_posted=NAN, job_type=NAN, is_remote=NAN))
    assert "date_posted" not in job
    assert "job_type" not in job
    assert "is_remote" not in job


def test_normalize_row_eur_currency():
    job = normalize_row(_row(currency="EUR", min_amount=40000.0, max_amount=60000.0))
    assert "€" in job["salary"]


def test_normalize_row_no_currency_defaults_to_dollar():
    job = normalize_row(_row(currency=""))
    assert job["salary"].startswith("$")
