"""Background worker turns must be EXEMPT from thin-router narrowing.

2026-08-13 himap-publish incident: TaskRunner background turns ran with the
thin-router cap active, so each background "worker" woke up router-only
(no vault/browser), re-delegated to another identical background worker,
and the chain (9104c160 -> 43465237 -> 5da50ada) looped until timeout with
0/8 articles published. The activation expressions must carry the
``is_background`` guard (source-pinned, same style as
test_inline_mutation_cap).
"""

from __future__ import annotations

import inspect

from lazyclaw.runtime import agent as agent_mod


def _source() -> str:
    return inspect.getsource(agent_mod)


def test_thin_router_activation_exempts_background() -> None:
    src = _source()
    idx = src.find('_os.environ.get("LAZYCLAW_THIN_ROUTER"')
    assert idx != -1, "thin-router activation expression not found"
    window = src[idx : idx + 300]
    assert 'not getattr(self, "is_background", False)' in window, (
        "thin-router activation lost the background-worker exemption — "
        "background turns ARE the delegated worker and must not be "
        "forced into router-only mode (delegation-loop incident)"
    )


def test_specialist_first_gate_exempts_background() -> None:
    src = _source()
    idx = src.find("if _specialist_first and (")
    assert idx != -1, "specialist-first gate not found"
    window = src[idx : idx + 300]
    assert 'not getattr(self, "is_background", False)' in window, (
        "specialist-first gate lost the background-worker exemption"
    )


def test_browser_sync_timeout_floor_and_bg_budget() -> None:
    """Timeout loop killer (2026-08-14): sync browser dispatches are
    floored at 480s (himap admin flows need 300-400s; the LLM kept
    choosing 240-300s) and background dispatches get 600s instead of
    inheriting TaskRunner's 300s default — shorter than sync budgets was
    self-defeating for the 'slow work' path."""
    from lazyclaw.skills.builtin import agent_tool as at

    assert at._BROWSER_SYNC_TIMEOUT_FLOOR_S >= 480
    assert at._BG_TIMEOUT_S >= 600
    src = inspect.getsource(at)
    assert 'if agent_type == "browser":' in src
    assert "timeout=_BG_TIMEOUT_S" in src
