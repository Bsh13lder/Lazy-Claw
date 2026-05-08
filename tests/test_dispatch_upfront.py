"""Verify the SCOPE ESTIMATE block lives in the user-facing plan prompt
so the brain plans dispatch UPFRONT instead of after running 3-4
sequential tools.

Pure prompt-level fix — these tests just inspect prompt text, no LLM
calls. The runtime side is covered by existing dispatch tests.
"""
from __future__ import annotations

from lazyclaw.runtime.taor import make_user_facing_plan_prompt


def test_scope_estimate_block_present():
    prompt = make_user_facing_plan_prompt(
        "research these 5 companies",
        ["dispatch_subagents", "web_search", "browser"],
    )
    assert "SCOPE ESTIMATE" in prompt
    assert "BEFORE your first tool call" in prompt


def test_scope_estimate_mentions_5_threshold():
    prompt = make_user_facing_plan_prompt(
        "do something",
        ["dispatch_subagents", "web_search"],
    )
    assert "≥ 5" in prompt or ">= 5" in prompt


def test_scope_estimate_recommends_dispatch_first():
    prompt = make_user_facing_plan_prompt(
        "do something",
        ["dispatch_subagents"],
    )
    # The whole point: recommend dispatch in the FIRST tool call when
    # the task is multi-target.
    assert "dispatch_subagents" in prompt
    assert "FIRST tool call" in prompt


def test_scope_estimate_appears_before_routing_rules():
    """Order matters — model reads top-to-bottom; SCOPE goes first."""
    prompt = make_user_facing_plan_prompt(
        "do something",
        ["dispatch_subagents", "web_search"],
    )
    scope_idx = prompt.find("SCOPE ESTIMATE")
    routing_idx = prompt.find("Routing rules")
    assert scope_idx >= 0
    assert routing_idx >= 0
    assert scope_idx < routing_idx


def test_keyword_routing_rules_still_present():
    """Backstop: the existing keyword detector ('for each' / 'all the X')
    must remain so legacy phrasings still trigger fan-out."""
    prompt = make_user_facing_plan_prompt(
        "for each item, do X",
        ["dispatch_subagents"],
    )
    assert "Multi-target work" in prompt
    assert "for each" in prompt
