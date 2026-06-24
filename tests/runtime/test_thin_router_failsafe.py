"""Phase 4b — the thin-router cap must keep the chat responsive.

When LAZYCLAW_THIN_ROUTER narrows the brain to meta-only, a brain that refuses
to delegate must still be auto-submitted to a background task (same guarantee
AUTO-PROMOTE gives), not left to run to max_iterations. The AUTO-PROMOTE
failsafes key on _force_dispatch_only, which the cap never sets — so the cap
needs its own cap-aware responsiveness failsafe.
"""
from __future__ import annotations
from pathlib import Path

_SRC = (Path(__file__).parent.parent.parent / "lazyclaw" / "runtime" / "agent.py").read_text()


def test_cap_sets_engaged_flag() -> None:
    # The cap block must record that it engaged (so the failsafe can key on it).
    idx = _SRC.index("THIN-ROUTER")
    # Window widened 2026-06-24: the work-call budget + dispatch-only
    # narrowing added lines between the block header and _thin_router_capped.
    window = _SRC[idx:idx + 3200]
    assert "_thin_router_capped" in window, (
        "the cap block must set a cap-engaged flag for the failsafe"
    )


def test_cap_aware_failsafe_exists() -> None:
    # A failsafe must auto-submit to task_runner when the cap engaged and the
    # brain refused to dispatch (mirrors the AUTO-PROMOTE failsafe).
    assert "_thin_router_capped" in _SRC
    # The cap-aware failsafe must reference the cap flag near a task_runner submit.
    fidx = _SRC.rindex("_thin_router_capped")
    region = _SRC[max(0, fidx - 1200): fidx + 1200]
    assert ".submit(" in region or "task_runner" in region, (
        "cap-aware failsafe must auto-submit to the background task runner"
    )
