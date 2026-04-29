"""Unit tests for _normalize_upwork_job — Upwork MCP response → canonical job dict."""

from __future__ import annotations

from lazyclaw.skills.builtin.survival.search_skill import _normalize_upwork_job


def test_normalize_basic_fields():
    job = _normalize_upwork_job({
        "id": "~01abc",
        "title": "Python scraper",
        "description": "Need python work",
        "budget": "$30",
        "skills": ["python", "scraping"],
        "url": "https://www.upwork.com/jobs/~01abc",
    })
    assert job["title"] == "Python scraper"
    assert job["budget"] == "$30"
    assert job["site"] == "upwork"
    assert job["platform"] == "upwork"
    assert job["is_remote"] is True


def test_normalize_falls_back_to_hourly_rate():
    job = _normalize_upwork_job({
        "id": "1",
        "title": "Long-term role",
        "hourly_rate": "$20-$40/hr",
    })
    assert job["budget"] == "$20-$40/hr"
    assert job["salary"] == "$20-$40/hr"


def test_normalize_resolves_relative_url():
    """Upwork sometimes returns just a ciphertext id; we prefix the canonical URL."""
    job = _normalize_upwork_job({
        "id": "~01abc",
        "title": "Tiny gig",
        "url": "~01abc",
    })
    assert job["url"] == "https://www.upwork.com/jobs/~01abc"


def test_normalize_handles_missing_skills():
    job = _normalize_upwork_job({"id": "1", "title": "x"})
    assert job["skills"] == []


def test_normalize_filters_non_string_skills():
    job = _normalize_upwork_job({
        "id": "1", "title": "x",
        "skills": ["python", 42, None, "scraping"],
    })
    assert job["skills"] == ["python", "scraping"]


def test_normalize_truncates_long_description():
    long = "x" * 1000
    job = _normalize_upwork_job({"id": "1", "title": "x", "description": long})
    assert len(job["description"]) == 500


def test_normalize_budget_default_na():
    job = _normalize_upwork_job({"id": "1", "title": "x"})
    assert job["budget"] == "N/A"


def test_normalize_alias_keys():
    """Upstream sometimes uses 'name' or 'snippet' instead of title/description."""
    job = _normalize_upwork_job({
        "id": "1", "name": "Aliased title", "snippet": "Aliased desc",
    })
    assert job["title"] == "Aliased title"
    assert job["description"] == "Aliased desc"


def test_normalize_id_falls_through_aliases():
    job = _normalize_upwork_job({"ciphertext": "~01ZZZ", "title": "x"})
    assert job["id"] == "~01ZZZ"


def test_normalize_compatible_with_score_job():
    """Output must be consumable by survival.matcher.score_job without errors."""
    from lazyclaw.survival.matcher import score_job
    from lazyclaw.survival.profile import SkillsProfile

    job = _normalize_upwork_job({
        "id": "~01abc", "title": "python scraper", "description": "scrape stuff",
        "budget": "$50", "skills": ["python"], "url": "~01abc",
    })
    profile = SkillsProfile(skills=("python",), max_tiny_gig_budget=100.0)
    scored = score_job(job, profile)
    assert scored.match_score > 0
    assert scored.platform == "upwork"
