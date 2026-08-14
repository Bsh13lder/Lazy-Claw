"""Specialists must be able to reach allowlisted MCP tools by bare suffix.

MCP tool ids are dynamic (`mcp_<server_uuid>_<name>`), so an exact-name
match against a specialist's static allowlist (e.g. `upwork_submit_proposal`)
can never hit them. Without unioning MCP tools in by their BARE suffix, the
`freelance_specialist` can DRAFT an Upwork proposal (`apply_job`) but never
SUBMIT it (`upwork_submit_proposal`), so it loops re-drafting forever
(2026-06-08 apply-loop regression).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from lazyclaw.teams.runner import _bare_tool_name, _filter_tools

# A realistic server UUID (dashes, no underscores) so the strip can't cheat by
# splitting on the wrong delimiter.
_MCP_SUBMIT = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_submit_proposal"


# ── _bare_tool_name ───────────────────────────────────────────────────


def test_bare_tool_name_strips_mcp_prefix():
    assert _bare_tool_name(_MCP_SUBMIT) == "upwork_submit_proposal"


def test_bare_tool_name_handles_uuid_with_dashes():
    # Server UUIDs use dashes (no underscores), so the bare name is
    # everything after the first two underscores — the dashed UUID counts
    # as a single segment.
    name = "mcp_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_apply_job"
    assert _bare_tool_name(name) == "apply_job"


def test_bare_tool_name_passes_through_non_mcp():
    assert _bare_tool_name("apply_job") == "apply_job"
    assert _bare_tool_name("upwork_submit_proposal") == "upwork_submit_proposal"


# ── _filter_tools unions allowlisted MCP tools by bare suffix ──────────


def _registry_with_submit_mcp() -> MagicMock:
    reg = MagicMock()
    reg.list_tools.return_value = [
        {
            "function": {
                "name": "apply_job",
                "description": "Draft an Upwork proposal.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "function": {
                "name": "search_jobs",
                "description": "Search freelance jobs.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    reg.list_mcp_tools.return_value = [
        {
            "function": {
                "name": _MCP_SUBMIT,
                "description": "Submit an Upwork proposal via mcp-upwork.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "function": {
                # An MCP tool that is NOT on the allowlist — must stay out.
                "name": "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_send_message",
                "description": "Send an Upwork DM.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    return reg


def test_filter_tools_unions_allowlisted_mcp_tool_by_suffix():
    reg = _registry_with_submit_mcp()
    out = _filter_tools(
        reg,
        ("apply_job", "upwork_submit_proposal"),
        include_scraper=False,
    )
    names = {t["function"]["name"] for t in out}
    # Native allowlisted tool present.
    assert "apply_job" in names
    # The MCP submit tool is reachable despite its dynamic id prefix.
    assert _MCP_SUBMIT in names
    # An MCP tool NOT on the allowlist stays out.
    assert (
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_send_message"
        not in names
    )


def test_filter_tools_does_not_union_unlisted_mcp_without_scraper():
    """If the bare suffix isn't allowlisted, the MCP tool is not offered."""
    reg = _registry_with_submit_mcp()
    out = _filter_tools(reg, ("apply_job",), include_scraper=False)
    names = {t["function"]["name"] for t in out}
    assert "apply_job" in names
    assert _MCP_SUBMIT not in names


# ── Source-inspection: freelance_specialist.md ────────────────────────


def test_freelance_specialist_lists_upwork_submit_proposal():
    md = (
        Path(__file__).resolve().parents[2]
        / "lazyclaw"
        / "teams"
        / "specialists"
        / "freelance_specialist.md"
    )
    text = md.read_text(encoding="utf-8")
    assert "upwork_submit_proposal" in text


def test_freelance_specialist_lists_upwork_send_message():
    """The Upwork send tool must be on the allowlist.

    2026-07-31: a "text James on Upwork" background task burned its whole
    300s budget re-reading the thread and poking the browser because
    `upwork_send_message` was missing from the allowlist — the specialist
    had every READ tool but no way to SEND (same class as the 2026-06-10
    email-specialist incident).
    """
    md = (
        Path(__file__).resolve().parents[2]
        / "lazyclaw"
        / "teams"
        / "specialists"
        / "freelance_specialist.md"
    )
    text = md.read_text(encoding="utf-8")
    assert "upwork_send_message" in text
