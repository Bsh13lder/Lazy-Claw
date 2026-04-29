"""Pure row-normalizer for python-jobspy DataFrame rows.

Kept separate from server.py so it's unit-testable without spawning
the MCP server or hitting the network. Handles the three pandas/jobspy
gotchas that bit the previous version:

* `bool(NaN) == True` — naive `if min_sal and max_sal` fires on missing
  salaries and emits "$nan-$nan".
* `str(NaN) == "nan"` — leaks "nan" into title/company/description when
  a field is missing.
* float salaries — jobspy returns floats so `f"${min_sal}"` produced
  "$50.0" instead of "$50".
"""

from __future__ import annotations

import math
from typing import Any, Mapping

_CURRENCY_SYMBOLS: Mapping[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "C$",
    "AUD": "A$",
}


def _is_nan(val: Any) -> bool:
    return isinstance(val, float) and math.isnan(val)


def _text(val: Any) -> str:
    """Coerce to string, mapping None/NaN to ''."""
    if val is None or _is_nan(val):
        return ""
    return str(val).strip()


def _num(val: Any) -> float | None:
    """Coerce to float, mapping None/NaN/non-numeric to None."""
    if val is None or _is_nan(val):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _bool(val: Any) -> bool | None:
    """Coerce to bool, mapping None/NaN to None (don't lie about missing)."""
    if val is None or _is_nan(val):
        return None
    return bool(val)


def _fmt_amount(val: float) -> str:
    """Format whole values as int, fractional as 2-decimal."""
    return f"{int(val)}" if val.is_integer() else f"{val:.2f}"


def _format_salary(
    min_amount: float | None,
    max_amount: float | None,
    interval: str,
    currency: str,
) -> str:
    """Render salary range. Empty string when neither bound is present."""
    # Defense in depth: also drop NaN here in case _num was bypassed.
    if _is_nan(min_amount):
        min_amount = None
    if _is_nan(max_amount):
        max_amount = None

    sym = _CURRENCY_SYMBOLS.get(currency.upper(), currency or "$") if currency else "$"
    iv = f"/{interval}" if interval else ""

    if min_amount is not None and max_amount is not None:
        if min_amount == max_amount:
            return f"{sym}{_fmt_amount(min_amount)}{iv}"
        return f"{sym}{_fmt_amount(min_amount)}-{sym}{_fmt_amount(max_amount)}{iv}"
    if min_amount is not None:
        return f"{sym}{_fmt_amount(min_amount)}+{iv}"
    if max_amount is not None:
        return f"up to {sym}{_fmt_amount(max_amount)}{iv}"
    return ""


def normalize_row(row: Mapping[str, Any], description_limit: int = 500) -> dict:
    """Convert a python-jobspy row (Series or dict) to a normalized job dict.

    Returns the canonical lazyclaw job shape with:
    * Strings free of "nan" leakage.
    * Salary formatted as ints when whole; "" when missing (NOT "$nan-$nan").
    * Currency symbol from `currency` column, defaulting to $.
    * Useful extras (date_posted, is_remote, job_type) preserved when present.
    """
    description = _text(row.get("description"))
    if description and description_limit and len(description) > description_limit:
        description = description[:description_limit]

    min_amount = _num(row.get("min_amount"))
    max_amount = _num(row.get("max_amount"))
    interval = _text(row.get("interval"))
    currency = _text(row.get("currency"))
    salary = _format_salary(min_amount, max_amount, interval, currency)

    job: dict[str, Any] = {
        "id": _text(row.get("id")),
        "title": _text(row.get("title")),
        "company": _text(row.get("company")),
        "location": _text(row.get("location")),
        "site": _text(row.get("site")),
        # job_url_direct skips the platform redirect when available.
        "url": _text(row.get("job_url_direct")) or _text(row.get("job_url")),
        "description": description,
        "salary": salary,
        # Kept under both keys for back-compat with downstream skills that
        # read `budget` (matcher.py + survival skills).
        "budget": salary or "N/A",
    }

    date_posted = _text(row.get("date_posted"))
    if date_posted:
        job["date_posted"] = date_posted

    job_type = _text(row.get("job_type"))
    if job_type:
        job["job_type"] = job_type

    is_remote = _bool(row.get("is_remote"))
    if is_remote is not None:
        job["is_remote"] = is_remote

    return job
