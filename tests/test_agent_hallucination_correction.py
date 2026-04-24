"""Unit tests for the hallucination-correction helpers in agent.py.

These cover the module-level helpers introduced to fix the silent
infinite retry loop when the LLM calls a tool that doesn't exist
(see plan: do-all-what-you-quizzical-backus).

The full agent loop is not exercised here — that requires fixture-heavy
setup. These tests lock in the correction-message shape so regressions
are caught fast.
"""

from __future__ import annotations

from lazyclaw.runtime import agent as agent_mod


class _StubRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = list(names)

    def list_names_by_prefix(self, prefix: str) -> list[str]:
        return sorted(n for n in self._names if n.startswith(prefix))


def test_parse_mcp_name_matches_uuid_prefix():
    parsed = agent_mod._parse_mcp_name(
        "mcp_ce2f19a7-bd2a-4259-a669-89b316165a46_delete_spreadsheet"
    )
    assert parsed is not None
    server_id, bare = parsed
    assert server_id == "ce2f19a7-bd2a-4259-a669-89b316165a46"
    assert bare == "delete_spreadsheet"


def test_parse_mcp_name_rejects_plain_name():
    assert agent_mod._parse_mcp_name("list_mcp_servers") is None
    assert agent_mod._parse_mcp_name("browser") is None
    # Underscore-heavy names without the uuid pattern shouldn't match.
    assert agent_mod._parse_mcp_name("mcp_not_a_uuid_here_tool") is None


def test_correction_for_mcp_hallucination_lists_real_siblings():
    server_id = "ce2f19a7-bd2a-4259-a669-89b316165a46"
    prefix = f"mcp_{server_id}_"
    real_names = [
        f"{prefix}list_spreadsheets",
        f"{prefix}create_spreadsheet",
        f"{prefix}modify_sheet_values",
        f"{prefix}list_drive_items",
        "something_else_unrelated",
    ]
    registry = _StubRegistry(real_names)
    bad = f"{prefix}delete_spreadsheet"
    out = agent_mod._build_hallucination_correction(
        bad, valid_names={"search_tools", "delegate"}, registry=registry,
    )
    # Must name the bad tool, surface actual siblings (short form), and
    # point at the native fallback.
    assert bad in out
    assert "list_spreadsheets" in out
    assert "create_spreadsheet" in out
    assert "does NOT exist" in out
    assert "google_run_task" in out.lower()


def test_correction_for_plain_hallucination_uses_last_segment():
    # Regression: old code did `.split('_')[0]` → search_tools('mcp') for
    # 'mcp_xxx'. New code uses last segment.
    registry = _StubRegistry([])
    out = agent_mod._build_hallucination_correction(
        "read_file_v2",
        valid_names={"browser", "run_command", "read_file", "write_file"},
        registry=registry,
    )
    assert "search_tools('v2')" in out
    # Must NOT say search_tools('read') (old split-on-first-underscore bug).
    assert "search_tools('read')" not in out


def test_correction_hint_for_user_suggests_trash_for_delete_spreadsheet():
    bad = "mcp_ce2f19a7-bd2a-4259-a669-89b316165a46_delete_spreadsheet"
    out = agent_mod._correction_hint_for_user(bad, registry=_StubRegistry([]))
    assert "google_run_task" in out
    assert "trash_drive_item" in out


def test_correction_hint_for_user_generic_for_unknown_plain_tool():
    out = agent_mod._correction_hint_for_user(
        "invented_tool", registry=_StubRegistry([]),
    )
    assert "rephrasing" in out.lower() or "tell me" in out.lower()
