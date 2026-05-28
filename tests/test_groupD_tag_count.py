"""Unit tests for the _tag_count helper in lazyclaw.lazybrain.graph.

D3 — Bug: the old expression
    len((row[4] or "[]").count('"') // 2 and row[4] or "[]")
parses as len((count // 2 and row[4]) or "[]") → returns len(json_string) (e.g.
48 for 4 tags) instead of the actual tag count (4).

Fix: extract a small _tag_count(raw) helper and change the expression to
    (raw or "[]").count('"') // 2
"""

from __future__ import annotations

import pytest

from lazyclaw.lazybrain.graph import _tag_count


@pytest.mark.unit
def test_none_gives_zero() -> None:
    assert _tag_count(None) == 0


@pytest.mark.unit
def test_empty_json_array_gives_zero() -> None:
    assert _tag_count("[]") == 0


@pytest.mark.unit
def test_single_tag_gives_one() -> None:
    assert _tag_count('["a"]') == 1


@pytest.mark.unit
def test_two_tags() -> None:
    assert _tag_count('["a","b"]') == 2


@pytest.mark.unit
def test_three_tags() -> None:
    assert _tag_count('["user","feedback","project"]') == 3


@pytest.mark.unit
def test_real_lazybrain_tags() -> None:
    # Four tags — a representative value from a real note row.
    raw = '["rolled-up","kind/rollup","owner/agent","archived"]'
    assert _tag_count(raw) == 4
