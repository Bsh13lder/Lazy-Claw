"""Score job relevance against user's skills profile.

Pure Python matching — no LLM calls. Fast and free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lazyclaw.survival.profile import SkillsProfile


@dataclass(frozen=True)
class ScoredJob:
    """A job listing with a relevance score."""

    job_id: str
    title: str
    platform: str
    budget: str
    budget_value: float
    is_hourly: bool
    description: str
    skills_required: tuple[str, ...]
    match_score: float
    match_reasons: tuple[str, ...]
    url: str


def score_job(job: dict, profile: SkillsProfile) -> ScoredJob:
    """Score a single job against the profile. Returns ScoredJob with 0-1 score."""
    score = 0.0
    reasons: list[str] = []

    title = job.get("title", "").lower()
    description = job.get("description", "").lower()
    skills_raw = job.get("skills", [])
    job_skills = (
        [s.lower() for s in skills_raw if isinstance(s, str)]
        if isinstance(skills_raw, list)
        else []
    )
    combined_text = f"{title} {description} {' '.join(job_skills)}"

    # Skill match (biggest weight: 0.6) — word-boundary matching
    matched_skills: list[str] = []
    for skill in profile.skills:
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, combined_text):
            matched_skills.append(skill)

    if profile.skills:
        skill_ratio = len(matched_skills) / len(profile.skills)
        score += skill_ratio * 0.6
        if matched_skills:
            reasons.append(f"Skills: {', '.join(matched_skills)}")

    # Budget match (0.2)
    budget_value = _parse_budget(
        job.get("budget", ""), job.get("salary", "")
    )
    is_hourly = bool(
        re.search(r"\b(hour|hr|/h)\b", job.get("budget", "").lower())
    )

    if is_hourly and profile.min_hourly_rate > 0:
        if budget_value >= profile.min_hourly_rate:
            score += 0.2
            reasons.append(
                f"Budget ${budget_value}/hr >= ${profile.min_hourly_rate}/hr min"
            )
        else:
            score -= 0.1
    elif not is_hourly and profile.min_fixed_rate > 0:
        if budget_value >= profile.min_fixed_rate:
            score += 0.2
            reasons.append(
                f"Budget ${budget_value} >= ${profile.min_fixed_rate} min"
            )
        else:
            score -= 0.1
    elif budget_value > 0:
        score += 0.1  # has budget, can't compare

    # Category match (0.1)
    for cat in profile.preferred_categories:
        if cat.lower() in combined_text:
            score += 0.1
            reasons.append(f"Category: {cat}")
            break

    # Recency bonus (0.05)
    score += 0.05

    # Exclusion filter — zeros the score (word-boundary matching)
    for keyword in profile.excluded_keywords:
        kw_pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
        if re.search(kw_pattern, combined_text):
            score = 0.0
            reasons = [f"Excluded: contains '{keyword}'"]
            break

    return ScoredJob(
        job_id=job.get("id", ""),
        title=job.get("title", ""),
        platform=job.get("site", job.get("platform", "")),
        budget=job.get("budget", job.get("salary", "N/A")),
        budget_value=budget_value,
        is_hourly=is_hourly,
        description=job.get("description", "")[:500],
        skills_required=tuple(job_skills),
        match_score=max(0.0, min(1.0, score)),
        match_reasons=tuple(reasons),
        url=job.get("url", job.get("job_url", "")),
    )


_BUDGET_RE = re.compile(r"\d+(?:\.\d+)?")


def _parse_budget(budget: str, salary: str) -> float:
    """Extract numeric value from budget/salary strings."""
    for text in [budget, salary]:
        if not text:
            continue
        # Strip commas first, then find plain numbers
        numbers = _BUDGET_RE.findall(text.replace(",", ""))
        if numbers:
            values = [float(n) for n in numbers]
            return sum(values) / len(values)  # average if range
    return 0.0
