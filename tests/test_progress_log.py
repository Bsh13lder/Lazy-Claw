"""Progress log helpers — kind/source normalization + decrypt-empty path.

These pin the pure helpers that don't need a DB so a regression in
shape (kind enum drift, source enum drift, JSON shape) shows up
without spinning up a full integration harness.
"""
from __future__ import annotations

import pytest

from lazyclaw.tasks import store


# ── Kind / source normalization ────────────────────────────────────────


@pytest.mark.parametrize("raw, expected", [
    ("started", "started"),
    ("working", "working"),
    ("progress", "progress"),
    ("STUCK", "stuck"),
    ("Blocked", "blocked"),
    ("paused", "paused"),
    ("resumed", "resumed"),
    ("done", "done"),
    ("pulse_fired", "pulse_fired"),
    ("garbage", "progress"),  # falls back to progress
    (None, "progress"),
    ("", "progress"),
])
def test_normalize_progress_kind(raw, expected) -> None:
    assert store._normalize_progress_kind(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("nl", "nl"),
    ("pulse", "pulse"),
    ("button", "button"),
    ("manual", "manual"),
    ("auto", "auto"),
    ("unknown", "manual"),  # falls back
    (None, "manual"),
    ("", "manual"),
])
def test_normalize_progress_source(raw, expected) -> None:
    assert store._normalize_progress_source(raw) == expected


# ── decrypt path on empty / garbage ────────────────────────────────────


def test_decrypt_progress_log_empty() -> None:
    """Empty / null DB value yields empty list, no exception."""
    assert store._decrypt_progress_log(None, b"\x00" * 32) == []
    assert store._decrypt_progress_log("", b"\x00" * 32) == []


def test_decrypt_progress_log_malformed_returns_empty() -> None:
    """Malformed encrypted blob → empty list, no raise."""
    assert store._decrypt_progress_log("not-an-envelope", b"\x00" * 32) == []
