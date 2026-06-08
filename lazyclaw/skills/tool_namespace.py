"""Native-vs-external tool namespace policy.

LazyClaw ships its OWN native, encrypted office suite (Sheets / Docs / PDF)
and many other native skills. A connected MCP server can expose a tool with
the SAME bare name — e.g. a Google Workspace server's ``create_sheet``
collides with the native ``create_sheet``. When that happens the native tool
is PRIMARY: the user gets LazyClaw's own tool unless they explicitly ask for
the external one ("make it in my Google sheet").

This module is the single source of truth for resolving that collision so
every call site behaves the same way:

* discovery (``search_tools``) drops the shadowed MCP duplicate — native wins;
* specialist tool-filtering does not union a shadowed MCP tool in by suffix;
* a call to a shadowed MCP tool is redirected to its native twin instead of
  failing with a confusing argument-validation error.

Background: 2026-06-08 the documents_specialist allowlisted ``create_sheet``
(native), but a Google Workspace MCP ``create_sheet`` was reachable via the
bare-suffix union (commit e83fdc2). The worker chased the Google tool, passed
``name=`` where it wanted ``spreadsheet_id=`` (404), then looped
``search_tools`` 27 times hunting for the "right" Google tool until the stuck
detector killed it.
"""

from __future__ import annotations

from collections.abc import Iterable


def is_mcp_tool_name(name: str) -> bool:
    """True for dynamic MCP tool ids (``mcp_<server_uuid>_<tool>``)."""
    return name.startswith("mcp_")


def bare_tool_name(name: str) -> str:
    """Strip the dynamic ``mcp_<server_uuid>_`` prefix.

    Server UUIDs use dashes (no underscores), so the bare tool name is
    everything after the first two underscores. Native names pass through
    unchanged. Mirrors the strip in ``runtime/agent.py`` / ``teams/runner.py``.
    """
    if name.startswith("mcp_"):
        parts = name.split("_", 2)
        if len(parts) == 3:
            return parts[2]
    return name


def native_names_from_tools(tools: Iterable[dict]) -> frozenset[str]:
    """Native tool names in an OpenAI-format tool list.

    Native = every tool whose name is not MCP-prefixed.
    """
    return frozenset(
        name
        for t in tools
        if (name := t.get("function", {}).get("name", "")) and not is_mcp_tool_name(name)
    )


def is_native_shadowed(name: str, native_names: Iterable[str]) -> bool:
    """True if ``name`` is an MCP tool whose bare name collides with a native one."""
    return is_mcp_tool_name(name) and bare_tool_name(name) in set(native_names)


def dedupe_prefer_native(tools: Iterable[dict], native_names: Iterable[str]) -> list[dict]:
    """Drop MCP tools whose bare name is already a native tool — native wins.

    MCP tools with a distinct name (no native twin) are kept, so the
    explicit-Google path (``create_google_sheet``, ``google_run_task`` …)
    stays discoverable.
    """
    native = set(native_names)
    return [t for t in tools if not is_native_shadowed(t.get("function", {}).get("name", ""), native)]


def shadow_redirect_message(mcp_name: str) -> str:
    """Tool-result text steering the model from a shadowed MCP tool to native."""
    native = bare_tool_name(mcp_name)
    return (
        f"Error: '{mcp_name}' is an external (e.g. Google Workspace) tool. "
        f"LazyClaw's own native '{native}' is PRIMARY — use '{native}' instead. "
        f"Only use the external tool when the user explicitly asked for Google / "
        f"their external account."
    )


def specialist_block_message(
    name: str,
    display_name: str,
    native_names: Iterable[str],
    allowed_skills: Iterable[str],
) -> str:
    """Message when a specialist's tool call is rejected.

    A shadowed-MCP call the specialist allowlisted by bare name gets a
    redirect to its native twin (so the worker self-corrects); anything else
    gets the plain "not available" deny.
    """
    if is_native_shadowed(name, native_names) and bare_tool_name(name) in set(allowed_skills):
        return shadow_redirect_message(name)
    return f"Error: Tool '{name}' is not available to {display_name}."
