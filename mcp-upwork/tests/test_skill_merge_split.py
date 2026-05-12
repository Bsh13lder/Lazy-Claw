"""Unit tests for _split_merged_skill.

Upwork's search-keyword highlighter wraps the matched substring in a
`<span class="highlight">`. When two skill chips render adjacent in
the tile DOM and one is highlighted, text_content() returns them
concatenated without whitespace — "PythonScripting" from [Python] +
[Scripting]. This test pins the conservative split rule so we don't
accidentally shred real CamelCase skill names like FastAPI / GraphQL.
"""

from __future__ import annotations

import pytest

from upwork_mcp.tools.jobs import _split_merged_skill


@pytest.mark.parametrize("merged,expected", [
    # Real merges seen in live MCP responses 2026-05-12
    ("PythonScripting",            ["Python", "Scripting"]),
    ("PythonMicrosoft Excel",      ["Python", "Microsoft Excel"]),
    ("PythonAutomation",           ["Python", "Automation"]),
    ("DataScraping",               ["Data", "Scraping"]),
    ("AIBuilder",                  ["AI", "Builder"]),
    # 3+ merged chips
    ("PythonAutomationMake.com",   ["Python", "Automation", "Make.com"]),
])
def test_split_merged_skill_splits_known_prefixes(merged, expected):
    assert _split_merged_skill(merged) == expected


@pytest.mark.parametrize("camel", [
    "FastAPI", "GraphQL", "JavaScript", "TypeScript",
    "PostgreSQL", "MongoDB", "MySQL", "PyTorch",
    "TensorFlow", "Node.js", "Next.js", "Vue.js",
])
def test_split_merged_skill_preserves_real_camelcase(camel):
    """Single-word CamelCase skill names must NOT be split.

    Without the allowlist + prefix-match-only rule, a naive
    [a-z][A-Z] split would produce "FastA" + "PI" for "FastAPI".
    Guard against any regression to that behavior.
    """
    result = _split_merged_skill(camel)
    assert result == [camel], f"{camel} got split to {result}"


@pytest.mark.parametrize("plain", [
    "Python", "Scrapy", "Selenium", "Django", "Automation",
    "Microsoft Excel", "Web Scraping", "Data Mining", "REST API",
])
def test_split_merged_skill_passes_through_plain(plain):
    """Strings with no merge (or with proper spaces) are returned as-is."""
    assert _split_merged_skill(plain) == [plain]


def test_split_merged_skill_empty_input():
    assert _split_merged_skill("") == []
