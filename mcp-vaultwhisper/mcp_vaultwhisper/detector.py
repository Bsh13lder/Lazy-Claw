"""PII detection — finds all PII matches in text with sequential placeholders."""
from __future__ import annotations

import re
from dataclasses import dataclass

from mcp_vaultwhisper.patterns import PIIPattern, PIIType


@dataclass(frozen=True)
class Detection:
    """A single PII detection result."""

    pii_type: PIIType
    value: str
    start: int
    end: int
    placeholder: str


def detect_pii(
    text: str,
    patterns: tuple[PIIPattern, ...],
) -> tuple[Detection, ...]:
    """Detect all PII in text, assigning sequential placeholders.

    Returns detections sorted by position (start index).
    Overlapping matches are deduplicated — the first match wins.
    """
    raw_matches: list[tuple[int, int, PIIType, str]] = []

    for pattern in patterns:
        try:
            for match in re.finditer(pattern.regex, text):
                raw_matches.append((
                    match.start(),
                    match.end(),
                    pattern.pii_type,
                    match.group(),
                ))
        except re.error:
            continue

    # Sort by start position, then longer match first for ties
    raw_matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

    # Deduplicate overlapping matches
    filtered: list[tuple[int, int, PIIType, str]] = []
    last_end = -1
    for start, end, pii_type, value in raw_matches:
        if start >= last_end:
            filtered.append((start, end, pii_type, value))
            last_end = end

    # Assign sequential placeholders per type
    type_counters: dict[str, int] = {}
    detections: list[Detection] = []

    for start, end, pii_type, value in filtered:
        type_name = pii_type.value
        count = type_counters.get(type_name, 0) + 1
        type_counters[type_name] = count
        placeholder = f"[{type_name}_{count}]"
        detections.append(Detection(
            pii_type=pii_type,
            value=value,
            start=start,
            end=end,
            placeholder=placeholder,
        ))

    return tuple(detections)
