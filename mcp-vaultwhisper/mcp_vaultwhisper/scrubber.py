"""PII scrubbing — replaces detected PII with placeholders."""
from __future__ import annotations

from dataclasses import dataclass

from mcp_vaultwhisper.detector import detect_pii
from mcp_vaultwhisper.patterns import PIIPattern


@dataclass(frozen=True)
class ScrubResult:
    """Result of scrubbing PII from text."""

    scrubbed_text: str
    mapping: dict[str, str]  # placeholder -> original value
    detection_count: int


def scrub(text: str, patterns: tuple[PIIPattern, ...]) -> ScrubResult:
    """Scrub all PII from text, replacing with placeholders.

    Builds the new string by replacing from end to start to preserve positions.
    Returns an immutable ScrubResult with mapping for restoration.
    """
    detections = detect_pii(text, patterns)

    if not detections:
        return ScrubResult(
            scrubbed_text=text,
            mapping={},
            detection_count=0,
        )

    # Build mapping
    mapping: dict[str, str] = {}
    for detection in detections:
        mapping[detection.placeholder] = detection.value

    # Replace from end to start to preserve character positions
    chars = list(text)
    for detection in reversed(detections):
        chars[detection.start:detection.end] = list(detection.placeholder)

    return ScrubResult(
        scrubbed_text="".join(chars),
        mapping=mapping,
        detection_count=len(detections),
    )
