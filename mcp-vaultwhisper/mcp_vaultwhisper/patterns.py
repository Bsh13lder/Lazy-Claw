"""PII pattern definitions — types, regexes, and sensitivity levels."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class PIIType(Enum):
    """Categories of personally identifiable information."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    IP_ADDRESS = "IP_ADDRESS"
    URL_WITH_TOKEN = "URL_WITH_TOKEN"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class PIIPattern:
    """A single PII detection pattern."""

    pii_type: PIIType
    regex: str
    description: str
    sensitivity: str  # "high" or "medium"


DEFAULT_PATTERNS: tuple[PIIPattern, ...] = (
    PIIPattern(
        pii_type=PIIType.EMAIL,
        regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        description="Email addresses",
        sensitivity="medium",
    ),
    PIIPattern(
        pii_type=PIIType.PHONE,
        regex=r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        description="US phone numbers",
        sensitivity="medium",
    ),
    PIIPattern(
        pii_type=PIIType.SSN,
        regex=r"\b\d{3}-\d{2}-\d{4}\b",
        description="Social Security Numbers",
        sensitivity="high",
    ),
    PIIPattern(
        pii_type=PIIType.CREDIT_CARD,
        regex=r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        description="Credit card numbers",
        sensitivity="high",
    ),
    PIIPattern(
        pii_type=PIIType.IP_ADDRESS,
        regex=r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        description="IP addresses",
        sensitivity="medium",
    ),
    PIIPattern(
        pii_type=PIIType.URL_WITH_TOKEN,
        regex=r"https?://[^\s]*(?:token|key|secret|password|auth)=[^\s&]+",
        description="URLs containing authentication tokens or secrets",
        sensitivity="high",
    ),
    PIIPattern(
        pii_type=PIIType.DATE_OF_BIRTH,
        regex=r"\b(?:0[1-9]|1[0-2])[/-](?:0[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b",
        description="Dates of birth (MM/DD/YYYY or MM-DD-YYYY)",
        sensitivity="medium",
    ),
)


def parse_custom_patterns(json_str: str) -> tuple[PIIPattern, ...]:
    """Parse custom patterns from a JSON string.

    Expected format: [{"regex": "...", "description": "...", "sensitivity": "high|medium"}]
    """
    try:
        raw = json.loads(json_str)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to parse custom patterns JSON: %s", exc)
        return ()

    if not isinstance(raw, list):
        logger.warning("Custom patterns must be a JSON array")
        return ()

    patterns: list[PIIPattern] = []
    for item in raw:
        if not isinstance(item, dict) or "regex" not in item:
            continue
        sensitivity = item.get("sensitivity", "medium")
        if sensitivity not in ("high", "medium"):
            sensitivity = "medium"
        patterns.append(PIIPattern(
            pii_type=PIIType.CUSTOM,
            regex=item["regex"],
            description=item.get("description", "Custom pattern"),
            sensitivity=sensitivity,
        ))
    return tuple(patterns)


def get_active_patterns(
    mode: str,
    custom_patterns_json: str | None = None,
) -> tuple[PIIPattern, ...]:
    """Return active patterns based on mode and custom additions.

    Strict mode: all default patterns + custom.
    Relaxed mode: only high-sensitivity defaults + custom.
    """
    if mode == "relaxed":
        base = tuple(p for p in DEFAULT_PATTERNS if p.sensitivity == "high")
    else:
        base = DEFAULT_PATTERNS

    custom = parse_custom_patterns(custom_patterns_json) if custom_patterns_json else ()
    return base + custom
