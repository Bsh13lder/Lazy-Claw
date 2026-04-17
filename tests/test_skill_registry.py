"""Smoke test: the skill registry boots cleanly with every builtin.

If `register_defaults` throws or any skill has a broken `to_openai_tool`
schema, the agent won't start and the README's "128 skills" claim is
embarrassingly false.
"""

from __future__ import annotations

import pytest

from lazyclaw.skills.registry import SkillRegistry


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    r.register_defaults(config=None)
    return r


def test_default_skills_register(registry: SkillRegistry) -> None:
    # We claim 128+ in the README. If this drops below 100 something
    # broke silently — don't let that ship.
    assert len(registry._skills) >= 100, (
        f"expected >=100 default skills, got {len(registry._skills)}"
    )


def test_base_discovery_skills_present(registry: SkillRegistry) -> None:
    """The meta-tools the LLM always sees upfront.

    Note: `delegate` and `run_background` are registered by cli.py after
    TaskRunner + team_lead exist. They're not in register_defaults and
    this test only covers what register_defaults provides.
    """
    for name in ("search_tools", "recall_memories", "save_memory"):
        assert registry.get(name) is not None, f"missing base skill {name!r}"


def test_new_phase_a_b_d_skills_registered(registry: SkillRegistry) -> None:
    """Guardrails — the browser canvas work shipped in 0.2.0 must be loaded."""
    expected = {
        # Phase A — browser canvas
        "share_browser_control",
        # Phase B — checkpoints
        "request_user_approval",
        # Phase D — templates
        "save_browser_template",
        "list_browser_templates",
        "delete_browser_template",
        "run_browser_template",
        "watch_appointment_slots",
    }
    missing = expected - set(registry._skills.keys())
    assert not missing, f"Phase A/B/D skills missing: {missing}"


def test_every_skill_has_valid_openai_tool_schema(registry: SkillRegistry) -> None:
    """Every skill must render a valid OpenAI tool schema — otherwise
    the LLM request fails at runtime."""
    for name, skill in registry._skills.items():
        schema = skill.to_openai_tool()
        assert isinstance(schema, dict), f"{name}: schema is not a dict"
        assert schema.get("type") == "function", f"{name}: wrong type"
        fn = schema.get("function", {})
        assert fn.get("name") == name, f"{name}: function.name mismatch"
        assert fn.get("description"), f"{name}: missing description"
        params = fn.get("parameters", {})
        assert params.get("type") == "object", f"{name}: parameters.type must be object"


def test_skill_categories_non_empty(registry: SkillRegistry) -> None:
    cats = registry.list_by_category()
    assert len(cats) >= 10, f"expected many categories, got {list(cats)}"
    # Sanity check some key categories exist
    for expected_cat in ("browser", "memory", "tasks"):
        assert any(expected_cat in c for c in cats.keys()), (
            f"no category matching {expected_cat!r} in {list(cats.keys())}"
        )


def test_search_tools_returns_results(registry: SkillRegistry) -> None:
    """The meta-tool the LLM uses for discovery must actually find things."""
    import asyncio
    search = registry.get("search_tools")
    assert search is not None

    # search_tools expects {query: str}; a common real-world query.
    result = asyncio.run(search.execute(user_id="test-user", params={"query": "browser"}))
    assert isinstance(result, str) and len(result) > 0
    # Should find at least the unified 'browser' skill.
    assert "browser" in result.lower()
