"""The synthetic brain fan-out consolidation turn must NOT be forced into
SPECIALIST-FIRST routing.

Bug (2026-06-23): the consolidation turn's prompt says "write ONE consolidated
summary — don't delegate", but it was enqueued as a normal turn, so
`_process_message_inner` injected the SPECIALIST-FIRST router guidance ("you are
a router, delegate everything") and filtered the toolset to meta+readonly. The
brain obeyed the routing, re-delegated/thrashed, and dumped raw ugly text to
Telegram instead of a summary. These tests pin the exemption.
"""
from __future__ import annotations

import re

from lazyclaw.runtime.consolidation_guidance import (
    CONSOLIDATION_TURN_PREFIX,
    is_consolidation_turn,
)

_AGENT_PY = (
    "/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/agent.py"
)
_TASK_RUNNER_PY = (
    "/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/task_runner.py"
)


def test_detects_consolidation_marker():
    msg = (
        "[Background fan-out complete — 2 tasks finished]\n\n"
        "Results from background tasks you spawned earlier:\n..."
    )
    assert is_consolidation_turn(msg) is True


def test_rejects_normal_messages():
    assert is_consolidation_turn("check my upwork inbox") is False
    assert is_consolidation_turn("") is False
    assert is_consolidation_turn(None) is False
    # leading whitespace still detected
    assert is_consolidation_turn("   " + CONSOLIDATION_TURN_PREFIX + " — 1 tasks finished]") is True


def test_task_runner_builds_marker_from_the_shared_constant():
    """Single-source the marker so detection can't silently drift."""
    src = open(_TASK_RUNNER_PY, encoding="utf-8").read()
    assert "CONSOLIDATION_TURN_PREFIX" in src, (
        "task_runner must build the consolidation message from the shared "
        "CONSOLIDATION_TURN_PREFIX constant (else detection drifts silently)"
    )


def test_agent_exempts_consolidation_from_specialist_first():
    """Both SPECIALIST-FIRST action points (guidance injection + tool filter)
    must be gated so they do NOT apply to a consolidation turn."""
    src = open(_AGENT_PY, encoding="utf-8").read()
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    # A consolidation flag must be computed and used to gate specialist-first.
    assert "is_consolidation_turn(" in code or "_is_consolidation" in code, (
        "agent.py must detect consolidation turns"
    )
    # The router-guidance injection must be conditioned on not-consolidation.
    guide = code.find("SPECIALIST-FIRST: router guidance injected")
    assert guide != -1
    gate = code.rfind("if _specialist_first", 0, guide)
    assert gate != -1
    assert "_is_consolidation" in code[gate:guide], (
        "the SPECIALIST-FIRST guidance gate must exclude consolidation turns"
    )
