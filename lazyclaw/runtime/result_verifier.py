"""Heuristic post-execution check for non-browser tool results.

The browser skill has its own ``action_verifier`` that compares before/after
page state and appends ``→ SUCCESS:`` / ``→ FAILED:`` markers. Every other
tool (n8n_run_workflow, email_send, run_command, ...) has **no** such check:
a tool that returns ``"Error: HTTP 400"`` flows straight into the LLM context
and the model often paraphrases it as success.

This module fills that gap with a cheap, pure-function pass. Output uses the
same ``→ FAILED:`` marker so the existing machinery (``stuck_detector``'s
``detect_no_progress`` and TAOR's ``verify_response``) picks it up for free.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["classify", "stamp_failed"]


# Tools that should never return an empty string — an empty result from them
# is a strong signal that something went wrong at the tool layer.
_EMPTY_MEANS_FAILED: Final[tuple[str, ...]] = (
    "read_file",
    "list_directory",
)

# Tools whose natural output is often empty/short (search with no hits,
# list with zero rows). Don't flag emptiness there.
_EMPTY_IS_OK_PREFIXES: Final[tuple[str, ...]] = (
    "search_",
    "list_",
    "recall_",
    "email_search",
    "whatsapp_search",
    "lazybrain_search_",
    "lazybrain_list_",
)

_HTTP_ERROR_RE: Final = re.compile(r"\bHTTP\s*[45]\d{2}\b|\b[45]\d{2}\s+(Bad Request|Unauthorized|Forbidden|Not Found|Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout)\b", re.IGNORECASE)

# Phrases that indicate a genuine failure. Each entry is a short reason
# label used in the final marker. Order matters — most specific first.
_FAILURE_MARKERS: Final[tuple[tuple[re.Pattern, str], ...]] = (
    (re.compile(r"^\s*Error\b", re.IGNORECASE),                       "error"),
    (re.compile(r"^\s*Exception\b", re.IGNORECASE),                   "exception"),
    (re.compile(r"^\s*Traceback\b"),                                  "traceback"),
    (re.compile(r"\bconnection refused\b", re.IGNORECASE),            "connection refused"),
    (re.compile(r"\btimed?\s*out\b", re.IGNORECASE),                  "timeout"),
    (re.compile(r"\bpermission denied\b", re.IGNORECASE),             "permission denied"),
    (re.compile(r"\bnot authori[sz]ed\b", re.IGNORECASE),             "not authorized"),
    (re.compile(r"\b(unauthori[sz]ed|forbidden)\b", re.IGNORECASE),   "auth rejected"),
    (re.compile(r"\bno such (file|directory)\b", re.IGNORECASE),      "not found"),
    (re.compile(r"\bcredentials? (missing|not set|empty)\b", re.IGNORECASE), "missing credentials"),
)


# Already marked by the browser action_verifier or an upstream classifier —
# don't double-stamp.
_MARKER_FAILED: Final = "→ FAILED:"
_MARKER_SUCCESS: Final = "→ SUCCESS:"


def classify(tool_name: str, result: str) -> tuple[str, str | None]:
    """Return ``(status, reason)`` where status is ``"success"``/``"failed"``/``"uncertain"``.

    Pure function — no side effects, no I/O. Keeps an empty ``reason`` on
    success so callers can use ``if status == "failed"`` without caring
    about the label.
    """
    if not tool_name:
        tool_name = ""
    if result is None:
        result = ""

    # Respect existing markers from browser action_verifier.
    if _MARKER_SUCCESS in result:
        return "success", None
    if _MARKER_FAILED in result:
        return "failed", None

    stripped = result.strip()

    # Empty result from a tool that must produce content.
    if not stripped:
        if tool_name in _EMPTY_MEANS_FAILED:
            return "failed", "empty result"
        if any(tool_name.startswith(p) for p in _EMPTY_IS_OK_PREFIXES):
            return "uncertain", None
        # A generic empty result is suspicious but not conclusive.
        return "uncertain", None

    # HTTP error codes — common when the agent calls n8n / external APIs.
    http_match = _HTTP_ERROR_RE.search(stripped)
    if http_match:
        return "failed", f"HTTP error ({http_match.group().strip()})"

    # Prefix / substring failure markers.
    for pattern, label in _FAILURE_MARKERS:
        if pattern.search(stripped):
            return "failed", label

    return "uncertain", None


def stamp_failed(result: str, reason: str) -> str:
    """Append ``→ FAILED: <reason>`` to a tool result so downstream machinery
    (stuck_detector, TAOR verify) sees the failure signal.
    """
    reason = (reason or "unknown").strip()
    if _MARKER_FAILED in result:
        return result  # already marked
    return f"{result.rstrip()}\n{_MARKER_FAILED} {reason}"
