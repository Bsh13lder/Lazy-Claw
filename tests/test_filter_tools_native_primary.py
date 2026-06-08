"""Specialist tool filtering: native office tools beat colliding MCP tools.

The 2026-06-08 documents_specialist incident: the specialist allowlists
`create_sheet` (the native encrypted sheet), but a connected Google Workspace
MCP server ALSO exposes `create_sheet`. The bare-suffix union (e83fdc2) made
the Google tool reachable, and the worker chased it instead of the native one.

Native must win the name; the MCP-only union (e83fdc2) must still work for
tools that have no native twin (e.g. `upwork_submit_proposal`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from lazyclaw.teams.runner import _filter_tools


def _reg() -> MagicMock:
    reg = MagicMock()
    native_create = {
        "function": {
            "name": "create_sheet",
            "description": "native encrypted spreadsheet",
            "parameters": {"type": "object", "properties": {}},
        }
    }
    native_read = {
        "function": {
            "name": "read_sheet",
            "description": "native",
            "parameters": {"type": "object", "properties": {}},
        }
    }
    google_create = {
        "function": {
            "name": "mcp_ce2f_create_sheet",
            "description": "Google Sheets create",
            "parameters": {"type": "object", "properties": {}},
        }
    }
    upwork = {
        "function": {
            "name": "mcp_abc_upwork_submit_proposal",
            "description": "upwork submit",
            "parameters": {"type": "object", "properties": {}},
        }
    }
    reg.list_tools.return_value = [native_create, native_read, google_create, upwork]
    reg.list_mcp_tools.return_value = [google_create, upwork]
    return reg


def test_filter_tools_prefers_native_sheet_over_google_duplicate():
    out = {t["function"]["name"] for t in _filter_tools(_reg(), ("create_sheet", "read_sheet"))}
    assert "create_sheet" in out
    assert "mcp_ce2f_create_sheet" not in out


def test_filter_tools_still_unions_mcp_only_tools_by_bare_suffix():
    # No native twin for `upwork_submit_proposal` → still reachable (e83fdc2).
    out = {t["function"]["name"] for t in _filter_tools(_reg(), ("upwork_submit_proposal",))}
    assert "mcp_abc_upwork_submit_proposal" in out
