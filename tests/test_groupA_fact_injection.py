"""Tests: 'fact' memory_type must be in AUTO_INJECT_TYPES and pass is_auto_inject_type.

Regression guard for the fact-trapdoor bug where every new agent memory was
classified as 'fact' but 'fact' was excluded from AUTO_INJECT_TYPES, silently
dropping it from context injection on every turn.
"""
from __future__ import annotations

import pytest

from lazyclaw.lazybrain.memory_types import AUTO_INJECT_TYPES, is_auto_inject_type


def test_fact_in_auto_inject_types():
    assert "fact" in AUTO_INJECT_TYPES


def test_is_auto_inject_type_fact_true():
    assert is_auto_inject_type("fact") is True


def test_is_auto_inject_type_session_log_false():
    assert is_auto_inject_type("session-log") is False


def test_is_auto_inject_type_none_false():
    assert is_auto_inject_type(None) is False


def test_is_auto_inject_type_other_false():
    assert is_auto_inject_type("other") is False


@pytest.mark.parametrize("mt", ["user", "feedback", "project", "reference", "fact"])
def test_is_auto_inject_type_all_expected_true(mt):
    assert is_auto_inject_type(mt) is True


@pytest.mark.parametrize("mt", ["session-log", "other", None, "", "unknown_type"])
def test_is_auto_inject_type_excluded_false(mt):
    assert is_auto_inject_type(mt) is False
