"""2026-08-20 plan-JSON leak — the browser action planner's plan block
must never reach the user.

`make_plan_injection_prompt` deliberately asks the model to put a plan
JSON block ({"goal", "steps":[{"description","success_criteria",
"fallback"}]}) in its VISIBLE content; every sanitizer (display token
emit, history write, history read) only stripped the XML <plan>/
<taor_plan>/<think> variants — so the raw JSON rendered as an unreadable
code block on the phone and was persisted to history.

Fix: `strip_plan_json_block` — schema-keyed removal (goal + steps with
description/success_criteria/fallback), applied at all three sites. It
must NEVER eat arbitrary user JSON that merely looks like JSON.
"""

from __future__ import annotations

import inspect

from lazyclaw.browser.action_planner import strip_plan_json_block

_PLAN_JSON = (
    '{\n  "goal": "Report the last 3 blog drafts",\n  "steps": [\n'
    '    {"description": "Dispatch a browser agent",\n'
    '     "success_criteria": "Titles, dates collected",\n'
    '     "fallback": "If the admin list fails, use the API"},\n'
    '    {"description": "Synthesize a report",\n'
    '     "success_criteria": "Single summary",\n'
    '     "fallback": "Report partial results"}\n  ]\n}'
)


def test_strips_fenced_plan_block() -> None:
    text = f"Here is my plan:\n```json\n{_PLAN_JSON}\n```\nStarting now."
    out = strip_plan_json_block(text)
    assert '"success_criteria"' not in out
    assert "```" not in out
    assert "Here is my plan:" in out
    assert "Starting now." in out


def test_strips_raw_unfenced_plan_block() -> None:
    text = f"{_PLAN_JSON}\nDispatching the agents."
    out = strip_plan_json_block(text)
    assert '"goal"' not in out
    assert "Dispatching the agents." in out


def test_keeps_non_plan_json_untouched() -> None:
    """A user's own JSON (different schema) must never be eaten."""
    text = (
        "Your config:\n```json\n"
        '{"host": "example.com", "port": 443, "steps": ["a", "b"]}\n'
        "```"
    )
    assert strip_plan_json_block(text) == text


def test_keeps_goal_steps_json_without_plan_step_shape() -> None:
    """Even goal+steps JSON is kept unless steps carry the planner's
    description/success_criteria/fallback shape."""
    text = '{"goal": "save money", "steps": ["walk", "cook at home"]}'
    assert strip_plan_json_block(text) == text


def test_invalid_json_untouched() -> None:
    text = 'Broken: {"goal": "x", "steps": [ oops'
    assert strip_plan_json_block(text) == text


def test_plain_text_untouched() -> None:
    assert strip_plan_json_block("no braces here") == "no braces here"
    assert strip_plan_json_block("") == ""


# ── wiring: all three sanitize sites call the stripper ───────────────


def test_display_and_history_write_sites_strip_plan_json() -> None:
    from lazyclaw.runtime import agent as agent_mod

    src = inspect.getsource(agent_mod)
    # Display path: right after the <think> strip before the token emit.
    think_idx = src.index('if "<think>" in _display_content:')
    window = src[think_idx:think_idx + 900]
    assert "strip_plan_json_block" in window, (
        "plan JSON must be stripped from _display_content before the "
        "token emit"
    )
    # History write path: alongside the XML strips.
    hist_idx = src.index("_history_content = _final_content")
    hist_window = src[hist_idx:hist_idx + 1200]
    assert "strip_plan_json_block" in hist_window, (
        "plan JSON must be stripped from _history_content or it persists "
        "and re-renders on every reload"
    )


def test_history_read_scrubber_strips_plan_json() -> None:
    from lazyclaw.gateway.routes.chat_history import _strip_internal_blocks

    stored = f"Working on it.\n```json\n{_PLAN_JSON}\n```"
    out = _strip_internal_blocks(stored)
    assert '"success_criteria"' not in out
    assert "Working on it." in out
    # The XML strips it always did must keep working.
    assert _strip_internal_blocks("<plan>x</plan>hello") == "hello"
