"""Native-vs-external tool namespace policy.

LazyClaw's native office suite (Sheets/Docs/PDF) and other native skills are
PRIMARY. When a connected MCP server exposes a tool with the same bare name
(e.g. a Google Workspace `create_sheet` colliding with the native one), the
native tool wins: the MCP duplicate is dropped from discovery + filtering and
a call to it is redirected to the native twin.

Regression guard for the 2026-06-08 documents_specialist stuck-loop — it
chased a Google `create_sheet` (wrong args → 404), looped search_tools ×27,
and was killed by the stuck detector.
"""

from __future__ import annotations

from lazyclaw.skills.tool_namespace import (
    bare_tool_name,
    dedupe_prefer_native,
    is_native_shadowed,
    native_names_from_tools,
    shadow_redirect_message,
    specialist_block_message,
)


def _tool(name: str, desc: str = "") -> dict:
    return {"function": {"name": name, "description": desc or name, "parameters": {}}}


# ── bare_tool_name ────────────────────────────────────────────────────


def test_bare_tool_name_strips_mcp_uuid_prefix():
    assert (
        bare_tool_name("mcp_ce2f19a7-bd2a-4259-a669-89b316165a46_create_sheet")
        == "create_sheet"
    )


def test_bare_tool_name_passes_through_native():
    assert bare_tool_name("create_sheet") == "create_sheet"


def test_bare_tool_name_keeps_underscores_in_the_bare_name():
    assert bare_tool_name("mcp_abc-123_append_sheet_rows") == "append_sheet_rows"


# ── native_names_from_tools ───────────────────────────────────────────


def test_native_names_excludes_mcp_tools():
    tools = [_tool("create_sheet"), _tool("mcp_uuid_create_sheet"), _tool("read_doc")]
    assert native_names_from_tools(tools) == frozenset({"create_sheet", "read_doc"})


# ── is_native_shadowed ────────────────────────────────────────────────


def test_mcp_tool_is_shadowed_when_native_twin_exists():
    assert is_native_shadowed("mcp_uuid_create_sheet", frozenset({"create_sheet"})) is True


def test_mcp_tool_not_shadowed_without_native_twin():
    # Preserves the e83fdc2 fix: genuine MCP-only tools stay reachable.
    assert (
        is_native_shadowed("mcp_uuid_upwork_submit_proposal", frozenset({"create_sheet"}))
        is False
    )


def test_native_tool_is_never_shadowed():
    assert is_native_shadowed("create_sheet", frozenset({"create_sheet"})) is False


# ── dedupe_prefer_native ──────────────────────────────────────────────


def test_dedupe_drops_mcp_duplicate_keeps_native_and_google_specific():
    native = frozenset({"create_sheet"})
    tools = [
        _tool("create_sheet", "native encrypted spreadsheet"),
        _tool("mcp_uuid_create_sheet", "Google Sheets create"),
        _tool("mcp_uuid_create_google_sheet", "Google Sheets via run_task"),
    ]
    out = {t["function"]["name"] for t in dedupe_prefer_native(tools, native)}
    assert "create_sheet" in out
    assert "mcp_uuid_create_sheet" not in out  # native wins
    # The explicit-Google path (distinct name, no collision) survives.
    assert "mcp_uuid_create_google_sheet" in out


# ── shadow_redirect_message ───────────────────────────────────────────


def test_shadow_redirect_points_to_native_twin():
    msg = shadow_redirect_message("mcp_uuid_create_sheet")
    assert "create_sheet" in msg
    assert msg.lower().startswith("error")


# ── specialist_block_message ──────────────────────────────────────────


def test_block_message_redirects_shadowed_mcp_to_native():
    native = frozenset({"create_sheet"})
    msg = specialist_block_message(
        "mcp_uuid_create_sheet", "Documents Specialist", native, ("create_sheet",)
    )
    assert "create_sheet" in msg
    # A redirect, not a dead-end deny.
    assert "not available" not in msg.lower()


def test_block_message_hard_denies_truly_unavailable_tool():
    native = frozenset({"create_sheet"})
    msg = specialist_block_message(
        "delete_everything", "Documents Specialist", native, ("create_sheet",)
    )
    assert "not available" in msg.lower()
