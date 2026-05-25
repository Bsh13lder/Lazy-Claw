"""Tests for the pre-split quarantine in compressor.compress_history.

The bug being closed: ``quarantine_polluted_history`` originally ran on
the POST-compression message list (``[summary, ...recent_30]``). Polluted
assistant rows older than the recent window were already baked into the
summary string by ``summarize_chunk`` / ``_quick_summary`` before the
quarantine ever saw them — so the brain mimicked yesterday's
hallucination on every subsequent turn via the summary.

The fix: ``_quarantine_decrypted_dicts`` runs on the FULL decrypted dict
list before the older/recent split, so polluted rows about to be
summarized get the QUARANTINE placeholder first.
"""

from __future__ import annotations

import pytest

from lazyclaw.memory.compressor import (
    _quarantine_decrypted_dicts,
    _quarantine_history_view,
)
from lazyclaw.llm.providers.base import LLMMessage


# ─── _quarantine_decrypted_dicts ──────────────────────────────────────────

def _polluted_assistant_dict(idx: int) -> dict:
    """Build a dict that the wikilink-in-quote filter will flag."""
    return {
        "id": f"msg-{idx}",
        "role": "assistant",
        "content": (
            "> James Blue (9:12 PM): I have a Mac [[computer]] "
            "and an iPad."
        ),
        "tool_name": None,
        "metadata": None,
        "has_tool_calls": False,
    }


def _clean_user_dict(idx: int) -> dict:
    return {
        "id": f"msg-{idx}",
        "role": "user",
        "content": f"hello {idx}",
        "tool_name": None,
        "metadata": None,
        "has_tool_calls": False,
    }


def _clean_assistant_dict(idx: int) -> dict:
    return {
        "id": f"msg-{idx}",
        "role": "assistant",
        "content": f"sure, response {idx}",
        "tool_name": None,
        "metadata": None,
        "has_tool_calls": False,
    }


def test_empty_input_returns_empty_list_not_same_reference():
    out = _quarantine_decrypted_dicts([])
    assert out == []
    # Returns a new list per contract — caller may mutate safely.


def test_clean_messages_pass_through_unchanged_by_reference():
    msgs = [_clean_user_dict(1), _clean_assistant_dict(2)]
    out = _quarantine_decrypted_dicts(msgs)
    assert len(out) == 2
    # Clean rows are reused by reference (no cloning needed).
    assert out[0] is msgs[0]
    assert out[1] is msgs[1]


def test_polluted_assistant_row_gets_quarantine_placeholder():
    msgs = [
        _clean_user_dict(1),
        _polluted_assistant_dict(2),
        _clean_user_dict(3),
    ]
    out = _quarantine_decrypted_dicts(msgs)
    assert len(out) == 3
    # Clean rows untouched.
    assert out[0] is msgs[0]
    assert out[2] is msgs[2]
    # Polluted row replaced.
    assert out[1] is not msgs[1]
    assert "QUARANTINED" in out[1]["content"]
    assert "[[computer]]" not in out[1]["content"]
    # Original dict NOT mutated.
    assert "[[computer]]" in msgs[1]["content"]


def test_quarantine_clears_tool_call_metadata_on_polluted_row():
    """A sanitized assistant body cannot validly anchor tool results.

    The matching tool rows will be dropped by `_to_llm_messages` validator,
    which is the correct behavior — a quarantined reply shouldn't have its
    side-effects replayed.
    """
    polluted = _polluted_assistant_dict(1)
    polluted["metadata"] = '[{"id":"call_1","name":"x","arguments":"{}"}]'
    polluted["has_tool_calls"] = True
    out = _quarantine_decrypted_dicts([polluted])
    assert out[0]["metadata"] is None
    assert out[0]["has_tool_calls"] is False
    # Original NOT mutated.
    assert polluted["metadata"] is not None
    assert polluted["has_tool_calls"] is True


def test_polluted_row_outside_recent_window_still_gets_quarantined():
    """The core regression: a polluted row at index 5 of a 40-message
    history was previously baked into the summary string before
    quarantine ran. Now the full list is sanitized first.
    """
    msgs = [_clean_user_dict(i) for i in range(40)]
    msgs[5] = _polluted_assistant_dict(5)
    out = _quarantine_decrypted_dicts(msgs)
    assert "QUARANTINED" in out[5]["content"]
    assert "[[computer]]" not in out[5]["content"]
    # Other rows untouched.
    for i in (0, 4, 6, 39):
        assert out[i] is msgs[i]


def test_input_list_is_never_mutated_even_when_pollution_present():
    msgs = [_polluted_assistant_dict(1)]
    snapshot_content = msgs[0]["content"]
    _ = _quarantine_decrypted_dicts(msgs)
    assert msgs[0]["content"] == snapshot_content


def test_missing_content_key_handled_gracefully():
    """Defensive: a dict missing 'content' should not crash the helper."""
    msgs = [{"id": "x", "role": "user", "metadata": None}]
    out = _quarantine_decrypted_dicts(msgs)
    assert len(out) == 1
