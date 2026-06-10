"""THIN-ROUTER cap vs late-inject loophole (2026-06-10 16:20 incident).

The thin-router narrows tools to meta-only after one inline action and
suppresses the removed names — but it can only suppress tools that were
IN the sent list. Domain tools the LLM remembers from history (e.g.
``upwork_inbox_check``) were re-entering through the late-inject-from-
registry path, letting the brain keep doing inline work instead of
delegating. The gate refuses non-meta late-injection while the cap is
engaged.

Companion fix: mcp-upwork ``empty_or_blocked`` reads with a wall
diagnosis (cloudflare_challenge / login_required) now count as error
results so the failure counter → browser fallback → 3-strikes chain
fires on blocked-but-"successful" reads. Ambiguous selector drift must
NOT count (a genuinely empty list is not a failure).
"""

from __future__ import annotations

import inspect
import re

from lazyclaw.runtime import agent as agent_mod
from lazyclaw.runtime.agent import _META_TOOLS


def _agent_source() -> str:
    return inspect.getsource(agent_mod)


# ── late-inject gate wiring ─────────────────────────────────────────


def test_late_inject_gates_on_thin_router_cap():
    src = _agent_source()
    # The late-inject loop must consult the cap state and the meta set
    # before injecting a remembered tool.
    gate = re.search(
        r"_thin_router_capped\s*\n?\s*and tc\.name not in _META_TOOLS",
        src,
    )
    assert gate is not None, (
        "late-inject path lost the thin-router cap gate — history-"
        "remembered domain tools can bypass the meta-only narrowing"
    )


def test_gate_blocks_before_schema_lookup():
    """The gate must short-circuit BEFORE get_tool_schema injection."""
    src = _agent_source()
    gate_pos = src.find("and tc.name not in _META_TOOLS")
    assert gate_pos != -1
    # The injection call that follows the gate in the same loop.
    inject_pos = src.find("get_tool_schema(tc.name)", gate_pos)
    assert inject_pos != -1
    between = src[gate_pos:inject_pos]
    assert "_resurrect_blocked.append(tc.name)" in between
    assert "continue" in between


def test_delegate_and_search_tools_are_meta():
    """Meta tools stay callable under the cap — the gate must never
    block the delegation/discovery path itself."""
    assert "delegate" in _META_TOOLS
    assert "search_tools" in _META_TOOLS


def test_incident_tools_are_not_meta():
    """The two tools that bypassed the cap on 2026-06-10 must be
    domain tools (i.e. the gate applies to them)."""
    assert "upwork_inbox_check" not in _META_TOOLS
    assert "upwork_last_conversation" not in _META_TOOLS


# ── blocked-diagnosis failure markers ───────────────────────────────


def test_err_markers_include_wall_diagnoses():
    src = _agent_source()
    assert '\'"diagnosis": "cloudflare_challenge"\'' in src
    assert '\'"diagnosis": "login_required"\'' in src


def test_err_markers_exclude_ambiguous_drift():
    """selector_drift_or_truly_empty may be a genuinely empty inbox —
    counting it as failure would spuriously trigger browser fallback
    on every empty contracts/offers list."""
    src = _agent_source()
    assert '"diagnosis": "selector_drift_or_truly_empty"\'' not in src
