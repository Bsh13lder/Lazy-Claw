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
