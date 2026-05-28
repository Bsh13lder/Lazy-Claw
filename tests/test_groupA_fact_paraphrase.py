"""Tests: 'fact' memory_type must NOT be paraphrase-class after the fix.

Regression guard for the fact-trapdoor bug where 'fact' was excluded from
_AUTHORITATIVE_TYPES, causing every default agent memory (classifier
fallback = 'fact') to be wrapped as [CACHED PARAPHRASE] on recall even
when the content was a legitimate authoritative note.
"""
from __future__ import annotations

import pytest

from lazyclaw.lazybrain.paraphrase_sanitizer import is_paraphrase_class


def test_fact_is_not_paraphrase_class():
    assert is_paraphrase_class("fact") is False


def test_session_log_is_paraphrase_class():
    assert is_paraphrase_class("session-log") is True


def test_none_is_paraphrase_class():
    assert is_paraphrase_class(None) is True


def test_empty_string_is_paraphrase_class():
    assert is_paraphrase_class("") is True


def test_user_is_not_paraphrase_class():
    assert is_paraphrase_class("user") is False


def test_feedback_is_not_paraphrase_class():
    assert is_paraphrase_class("feedback") is False


def test_project_is_not_paraphrase_class():
    assert is_paraphrase_class("project") is False


def test_reference_is_not_paraphrase_class():
    assert is_paraphrase_class("reference") is False


def test_other_is_paraphrase_class():
    assert is_paraphrase_class("other") is True


def test_unknown_value_is_paraphrase_class():
    assert is_paraphrase_class("unknown_custom_type") is True


@pytest.mark.parametrize("mt", ["user", "feedback", "project", "reference", "fact"])
def test_authoritative_types_not_paraphrase(mt):
    assert is_paraphrase_class(mt) is False


@pytest.mark.parametrize("mt", ["session-log", "other", None, "", "whatever"])
def test_non_authoritative_types_are_paraphrase(mt):
    assert is_paraphrase_class(mt) is True
