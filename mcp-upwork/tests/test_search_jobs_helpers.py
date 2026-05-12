"""Unit tests for jobs.py helpers — _clean_posted + search URL canonical form.

The URL test guards against silently reverting to /best-matches (which
limits the search to the user's ~30 personalized recommendations
instead of the open 100k+ job board).
"""

from __future__ import annotations

import pytest

from upwork_mcp.tools.jobs import _clean_posted


@pytest.mark.parametrize("raw,expected", [
    # Real Upwork search-results render
    ("Posted\n            \n              6 hours ago", "6 hours ago"),
    ("Posted: 2 days ago", "2 days ago"),
    ("Posted - 30 minutes ago", "30 minutes ago"),
    ("  6 hours ago  ", "6 hours ago"),
    # Edge cases
    ("", ""),
    ("Posted", ""),
    ("   ", ""),
    # Should not over-strip — only the literal "Posted" prefix
    ("Posted yesterday at 5pm", "yesterday at 5pm"),
])
def test_clean_posted(raw, expected):
    assert _clean_posted(raw) == expected


def test_search_url_uses_global_jobs_endpoint():
    """search_jobs MUST query /nx/search/jobs/, never /nx/find-work/best-matches.

    /best-matches restricts the result set to ~20-50 personalized
    recommendations; the global search board has 100k+ active postings.
    Silent regression to /best-matches would shrink discoverability by
    >99% without raising any error. Guard against it explicitly by
    parsing the base_url assignment, not by raw substring match (so the
    comment block explaining why we're NOT on /best-matches doesn't
    falsely trip the regression alarm).
    """
    import inspect
    import re

    from upwork_mcp.tools import jobs as jobs_module

    source = inspect.getsource(jobs_module.search_jobs)
    # Find the base_url assignment line
    match = re.search(r'base_url\s*=\s*[\'"]([^\'"]+)[\'"]', source)
    assert match is not None, "search_jobs has no base_url string assignment"
    base = match.group(1)
    assert base.startswith("https://www.upwork.com/nx/search/jobs"), (
        f"search_jobs base_url is {base!r} — should target the global "
        f"/nx/search/jobs/ endpoint, not the personalized best-matches feed"
    )
