"""explore + general_purpose are declarative builtin specialists."""
from lazyclaw.teams.specialist_loader import load_builtin_specialists


def _by_name():
    return {s.name: s for s in load_builtin_specialists()}


def test_explore_loads():
    spec = _by_name()["explore"]
    assert spec.include_scraper is True
    assert "web_search" in spec.allowed_skills
    assert "read_file" in spec.allowed_skills
    assert spec.is_builtin is True
    assert "read-only" in spec.system_prompt.lower()


def test_general_purpose_loads_with_wildcard():
    spec = _by_name()["general_purpose"]
    assert spec.allowed_skills == ("*",)
    assert spec.is_builtin is True


def test_aliases_pick_up_new_builtins():
    # specialist_aliases builds from BUILTIN_SPECIALISTS at import time;
    # a fresh import in a fresh process would include them. Here we only
    # assert the loader output, since BUILTIN_SPECIALISTS is import-cached.
    names = set(_by_name())
    assert {"explore", "general_purpose"} <= names
