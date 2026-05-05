"""Pin the dict-shape MCP error detection.

Many MCP tools (mcp-upwork submit_proposal, mcp-scraper, etc.) return
Python dicts that get JSON-encoded into the result string —
e.g. ``{"status": "error", "message": "Apply button not found..."}``.
The string starts with ``{"`` and not any of the legacy prefixes
(``[MCP ERROR]``, ``Error:``, ...), so before this fix the 3-strikes
counter never incremented for the most common failure shape and
``skill_lesson`` stayed at ``outcome=pending`` even on hard failures.

Test by static-source check (same pattern as
``test_agent_force_dispatch.py``) since the full agentic loop is
heavy to mock.
"""

from __future__ import annotations

from pathlib import Path


_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def test_dict_error_detection_block_present() -> None:
    """The dict-shape detector must guard against the most common shapes."""
    assert "_err_status_markers" in _AGENT_SRC
    # All five canonical status strings + isError boolean shapes
    for marker in (
        '\'"status": "error"\'',
        '\'"status": "unknown"\'',
        '\'"status": "failed"\'',
        '\'"isError": true\'',
    ):
        assert marker in _AGENT_SRC, f"Missing dict-error marker: {marker}"


def test_dict_error_detection_handles_repr_quotes() -> None:
    """Both json.dumps double-quoted and repr() single-quoted forms covered."""
    assert "\"'status': 'error'\"" in _AGENT_SRC
    assert "\"'isError': True\"" in _AGENT_SRC


def test_dict_error_detection_only_scans_head() -> None:
    """Don't scan multi-MB results — head-only check keeps it cheap."""
    assert "head = result[:512]" in _AGENT_SRC


def test_dict_error_detection_gated_on_starts_with_brace() -> None:
    """Skip the dict scan for plain-string results."""
    assert 'result.startswith(("{", "["))' in _AGENT_SRC
