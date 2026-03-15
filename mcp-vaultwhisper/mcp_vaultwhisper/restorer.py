"""PII restoration — re-injects original values from placeholder mapping."""
from __future__ import annotations


def restore(text: str, mapping: dict[str, str]) -> str:
    """Replace each placeholder in text with its original value.

    Returns a new string with all placeholders restored.
    Placeholders not found in mapping are left unchanged.
    """
    result = text
    for placeholder, original in mapping.items():
        result = result.replace(placeholder, original)
    return result
